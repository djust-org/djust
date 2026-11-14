# Explicit exposure

A LiveView runs under explicit exposure
([ADR-038](https://github.com/djust-org/djust/blob/main/docs/adr/038-explicit-context-and-state-exposure.md)):
an attribute reaches a template, the session or the browser only when you say
so. This is the default since djust 2.0; a view does not have to ask for it.

Before 2.0 the default was the `legacy` policy, in which every public attribute
you set on `self` became template context, and much of it also ended up in
session state and the client state mirror. A view can still state
`exposure_policy = "legacy"` to keep that behaviour while it is migrated. See
[Keeping the legacy policy](#keeping-the-legacy-policy).

```python
from djust import LiveView, event_handler
from djust.decorators import state


class CounterView(LiveView):
    template_name = "counter.html"

    count = state(0, persist="server")

    @event_handler()
    def increment(self):
        self.count += 1
```

`{{ count }}` renders because `count` is declared with `state()`: a declared
field is template context without a `get_context_data()`. `self.service`,
`self.object`, `self.internal_note` and every other ordinary attribute stay in
server memory, available to your handlers and never exported.

## Where values can go

| Destination | Comes only from |
| --- | --- |
| Template context | Declared `state()` fields, `get_context_data()` additions and registered framework providers |
| Server-side saved state (reconnect, cross-worker restore, HTTP fallback) | The view's own state: public attributes, the `_private` ones `mount()` creates, `state()` fields. Never sent to the browser |
| Raw browser data (`get_state()`, HTTP API `assigns`) | `state(..., client=True)` fields |
| Back-navigation snapshot | `state(..., persist="client", client=True)` fields |
| Debug panel, time travel, bug capture | A redacted projection: names and types, not values |

A value you render is visible in the HTML, as with any Django template. Being
rendered does not also put it in the state mirror, the snapshot or the debug
panel: a declared field renders, but it reaches `get_state()` only with
`client=True`.

**Exposure is about the client; saving is automatic.** Keeping a value on the
server exposes nothing, so it needs no declaration: the view's state is saved
in the server-side session after each event, as in 1.x, and a later request
rebuilds the view from it. Saving a value does not render it, send it, or let
the client set it. See [what survives](../guides/state-survival.md).

### What the template sees

`get_context_data()` stays for derived and render-only values, and it can
override what a declared field renders as. Values are settled in this order,
the later one winning:

1. Declared `state()` fields (not `state(render=False)`).
2. `get_context_data()`: keyword arguments to `super().get_context_data()`, and
   keys set on its result.
3. Django context processors fill only names nothing above supplied.

A declared field shadows a context-processor value of the same name, as a
`get_context_data()` key does (`messages = state(default_factory=list)` is
fine). `user` and `perms` are flagged, because templates use them for
authorization: system check `djust.V027` and, with `DEBUG = True`, a warning
on the `djust.exposure` logger.

A name a framework provider owns (`online_count` on a `PresenceMixin` view, a
formset name, `streams`, `csrf_token`) is rendered by its provider, never from
a declaration of the same name. A framework-reserved name (`request`,
`view`, `kwargs`, `template_name`, `temporary_assigns`, a `LiveView` method)
cannot be a `state()` field at all: the view class raises
`ExposureConfigurationError` naming the field when it is created, whether the
field is declared on the view, a mixin or a base class (#4070). A `get_context_data()` that returns a new dict without calling
`super().get_context_data()` supplies no declared field (nor any provider).

The rule applies to `state()` only. A `@computed` value, an `assign_async()`
result and an ordinary attribute still have to be returned from
`get_context_data()`.

## Declaring state

```python
from djust.decorators import state

count = state(0)                                 # reactive, rendered, saved server-side
scratch = state(0, persist=False)                # never saved: starts from its default
cart = state(default_factory=list, persist="server")  # saved strictly: an error if it cannot be
page = state(1, persist="client", client=True)   # restorable on back-navigation
token = state("", persist="server", render=False)  # handlers only, never template context
```

- Every declared field is template context unless it says `render=False`. Use
  that for a value only handlers need. Declare a large field the template never
  reads `render=False`: everything in the render context is fingerprinted for
  change detection on every turn. A legacy view refuses
  `render=False`, because its attribute walk renders every public field.

- A field with no `persist=` is saved server-side with the rest of the view's
  state, bound to the session, the user, the tenant and the route.
  `persist=False` keeps a field out.
- `persist="server"` is the strict form: a value that cannot be saved, or a
  change over the declared-field limit, is refused with an error.
- `client=True` lets the raw value reach the browser.
- `persist="client"` requires `client=True`. A contradictory declaration fails
  at class definition.
- Only JSON primitives (strings, numbers, booleans, `None`, and lists or dicts
  of them) can be persisted. `Decimal`, dates, `UUID` and model instances are
  rejected rather than stringified. Store an id and load the object again in
  `get_context_data()`.

## Supported configuration

- **Sessions.** Server persistence needs a server-side session backend: `db`,
  `cached_db`, `cache` or `file`. Cookie sessions are browser-readable, so they
  are rejected: a request of a view with `persist="server"` state raises
  `UnsupportedSessionEngine` (an `ImproperlyConfigured`) naming the engine, and
  system check `djust.C030` reports the combination at startup.
- **Size.** Persisted state is bounded: 64 KB encoded, 10,000 values and
  nesting depth 16 per view. A change that would take the declared
  `persist="server"` fields over the limit is refused for that event: nothing
  is saved, the user gets an error naming the field and the limit
  (`code="state_too_large"`; HTTP status 413 on the fallback), and the view goes
  back to the last saved state (and `handle_restore()` runs, as after a
  reconnect), so later events work. What the refused handler queued for after
  its turn (`start_async` work, push events, flash, navigation, deferred
  callbacks) is discarded with it. Saved state is per session and route, not
  per browser tab: going back to "the last saved state" means what any tab of
  that session last saved on that route, so a refusal in one tab can bring in
  another tab's change. If an earlier turn's save was deferred and has not
  landed, the refusal is the reload error instead, so that turn's change is not
  dropped silently. Bound what a field can hold in your handler (a count cap
  does not bound bytes: one long text value is enough).
- **`DJUST_SERVER_STATE_MAX_AGE`.** The lifetime of a server envelope in
  seconds, from 1 to 86400. The default is 3600. System check `djust.C020`
  validates it.
- **`DJUST_EXPLICIT_STATE_SAVE_TIMEOUT`.** How long a turn waits for its
  state save, in seconds: greater than 0 and at most 10. The default is 2
  (it was 0.15 before 2.0). A save that takes longer gets the `transient`
  error described under [What changes at runtime](#what-changes-at-runtime).
  A larger value keeps more events' own updates when storage is slow, at the
  price of holding the page's other events, ticks and pushes for that long;
  a smaller one answers sooner and falls back to the full-HTML catch-up more
  often. The time counts from when the save starts running, not from when it
  is queued behind other sessions' work. System check `djust.C024` validates
  it.
- **`DJUST_STATE_SNAPSHOT_MAX_AGE` / `DJUST_STATE_SNAPSHOT_ENABLED`.** The
  signed back-navigation snapshot's lifetime, and its master switch. The
  lifetime is a whole number of seconds from 1 to 86400, 3600 by default; a
  larger value is capped at 86400 and anything else falls back to 3600, with
  a warning in the log. The service worker expires stored snapshots after the
  same age.
- **Schema changes.** Set `exposure_schema_version = 2` on the view when you
  change its declared fields. Older envelopes are then rejected and the view
  mounts fresh. Define `migrate_state(self, old_version, values)` to translate
  instead. Its result is validated like any other input.

## What changes at runtime

- **Every turn is authorized again.** Events, background results, `url_change`,
  ticks, `server_push` and `db_notify` turns reload the session and re-run
  authorization before your code runs. A revoked session gets an error and the
  socket closes with code 4403.
- **A session that no longer exists mounts fresh.** When the browser's session
  cookie names a session the store has lost (a cache flush or restart,
  eviction, expiry), the mount runs `mount()` under a new, anonymous session,
  as a page load would. Nothing is restored from the lost session. Over a
  WebSocket the replacement never reaches the browser, so it expires after
  `DJUST_SERVER_STATE_MAX_AGE`, and repeated mounts on one socket reuse it. Over
  SSE the stream response issues it as a cookie with Django's normal lifetime,
  as a page load does. An HTTP fallback POST whose session is gone mounts
  fresh the same way. The browser's cookie decides this even when the
  socket's connect-time sign-in check already read the lost session. A socket
  with no session cookie at all is still refused. Between two events on an
  open socket or stream a lost session is treated as a revocation (as a
  logout is, above); the next page load or reconnect mounts fresh.
  With `cache` sessions, Django treats a failed cache read as a missing
  session, so a transient cache error on mount also takes this path: that
  socket mounts anonymous while the real session survives.
- **A failed state save is reported, not hidden.** The client gets a
  `state_error` instead of an update, and a failed root save revokes its
  back-navigation snapshot. So the browser never shows a state that storage
  does not have.
- **Events on one page are saved one at a time, in order.** A turn waits for
  its save before it answers, and the page's next event starts after that.
  So events sent in quick succession (fast clicks, typing) all take effect,
  each gets its own update, and storage ends with the last one. A late write
  never replaces a newer one.
- **A save slower than `DJUST_EXPLICIT_STATE_SAVE_TIMEOUT` is deferred.** The
  event gets a `state_error` marked `transient` instead of its update, which
  does not ask the user to reload: the change is kept on the server, and once
  storage answers one catch-up update saves the newest state and sends full
  HTML. If the catch-up cannot finish, storage keeps missing the deadline
  (three deferrals in a row), or a save has been running for 10 seconds, the
  error is the reload error instead. At mount there is no page to update yet,
  so a save that outruns the deadline there is also the reload error.
- **What waits while a save is in storage.**
  - *Over a WebSocket* the save runs on the session's synchronous thread,
    and reading the socket's next frame needs that thread too. So the page's
    later events are not read until the write returns; they then run in
    order. They are not refused (the 64-turn `view_busy` cap of a page with
    several views is not reached this way) and do not count as further
    deferrals. With the default `worker_threads = None` that thread is shared
    by every WebSocket session of the process, so one slow write delays the
    synchronous work of all of them until it returns (see
    [Scaling](../guides/scaling.md)).
  - *Over SSE* each event POST has a thread of its own and saves run on a
    separate pool, so other sessions are not held. A later event of the same
    page runs while the earlier save is still in storage, waits the deadline
    for it and is deferred as well (this is where three deferrals in a row
    happen); the one catch-up covers them. A save that waits more than 5
    seconds for a pool thread is deferred too, so an SSE event can be held for
    up to those 5 seconds plus the save deadline before it is answered.
  - A `tick` and a `db_notify` that arrive meanwhile are skipped, not queued.
    A `server_push` is queued and applied afterwards, in order.
- **A save still running after a logout is dropped.** Over SSE a save can
  outlive the request that started it. Before it writes, it looks the session
  up once, and it writes nothing if the session was logged out, flushed or
  rotated away in another request, or now belongs to another user. With `cache` sessions a logout
  that lands in the moment between that lookup and the write can still be
  overwritten, as with any two concurrent Django requests; `db`, `cached_db`
  and `file` sessions refuse that write themselves.
- **A save writes only what the view changed.** The envelope, and any other
  `request.session[...]` write made during the turn, are merged into the session
  as storage holds it right before the write, so a value another request stored
  during the turn (an idle-activity timestamp, say) is neither overwritten nor
  rolled back. See [What the save writes to the
  session](../guides/navigation.md#what-the-save-writes-to-the-session) for the
  backends and the remaining race.
- **Errors follow Django.** With `DEBUG = True`, an explicit view's failure
  shows its exception and traceback, as Django's development output does: the
  technical 500 page, detailed error frames and dev overlay, and full log lines.
  With `DEBUG = False` it is value-free: a generic 500 page or error frame and
  a static log line. The debug panel, time travel and bug capture always show
  redacted values.
- **Service-worker caches.** Explicit pages are never written to the worker's
  page-shell or VDOM caches. The caches are cleared when the logged-in identity
  changes.
- **Actions.** A failed `@action` records the generic "Action failed". Raise
  `djust.decorators.ActionError("...")` to show your own message.
- **Presence.** `track_presence()` no longer adds `username` or `user_id` to
  the metadata peers receive.

## Framework features

- **Forms.** `FormMixin` provides `form_data`, `form_errors`, `field_errors`,
  `is_valid` and its messages to the template. Input and errors are saved
  server-side with the view's state, except password, one-time-code and
  payment-card inputs and names on the sensitive-field list: such input is
  never stored, and an error message that contains it is replaced.
  `form_input = persisted_form_input("name", "email")` from `djust.forms` is
  the strict, declared form of the same.
  Opting in a password field, or a name on the sensitive-field list, is
  refused at class definition (at mount, for a form class chosen by
  `get_form_class()`). Such fields render empty and never reach storage,
  frames or debug output.
- **Other mixins.** Each of these provides the names it publishes, render-only:
  `PresenceMixin` (`online_count`), `TutorialMixin` (`tutorial_running`,
  `tutorial_current_step`, `tutorial_total_steps`, `default_highlight_class`),
  `DataTableMixin` (its `table_*` configuration and state, not `table_model` or
  `table_queryset`), `FormSetHelpersMixin` (the formsets named in
  `formset_classes`) and `ThemeMixin` (`theme_preset`, `theme_mode`,
  `theme_resolved_mode` and the rendered theme values). List the mixin before
  `LiveView`; `DataTableMixin` and `FormSetHelpersMixin` refuse the other order
  on an explicit view. Apart from `ThemeMixin`'s, these names are reserved for
  the mixin: supplying one yourself in `get_context_data()` is a collision
  error (declaring it, `online_count = state(0)`, is fine). A `formset_classes`
  key that cannot be a template name (an underscore name, `streams`) is logged
  and that formset is not rendered.
- **WebSocket middleware.** An explicit mount binds to the page's session and
  to the request's user. Behind `AuthMiddlewareStack` the user is the one the
  stack authenticated. Behind the session-only `DjustMiddlewareStack` the
  request has no user: a `login_required` view is refused there, and a view
  that passes the access check reads its user from the session
  (`django.contrib.auth.get_user`), at mount and at every event. That user is
  for the view, not for access. Every access decision is still made against
  what the stack supplied, so behind the session-only stack `login_required`,
  `permission_required` (on a view or a handler), `check_permissions`,
  `has_object_permission`, `on_mount` hooks and the access control on `post()`
  all see no user and refuse, for a child view as for a page. What does see the
  session's user: the view's own code (`mount`, handlers, `get_object`,
  `get_context_data`), the binding of persisted state, presence identity,
  rate-limit keys and tenant resolution. A consumer routed with no session
  middleware is refused.
- **Uploads.** `uploads` renders each entry's progress and a sanitized file
  name. The raw client file name and the writer's result are not included.
  Uploads in flight are not resumed after a reconnect; the client registers
  them again.
- **Components.** Declare a `LiveComponent` at class level (`nav = Tabs(...)`).
  Each view gets its own copy, and its events reach it by `component_id`. A
  State-less `LiveComponent` (no `State` class) that a handler changes in place
  must call `self.trigger_update()` for the change to render. Do not hold a
  `LiveComponent` in a `state()` field or in an unregistered private attribute: its events
  then fail with "Component not found". Component state is transient: a
  reconnect mounts it fresh.
- **Plain components** (`Component` subclasses such as `Badge` or `Rating`) are
  refused in the class body: a plain component has no descriptor, so a class
  body one is a single object shared by every session, and a handler's write to
  it would reach every user. Declare it as a field, which renders it, or build
  it in `get_context_data()`:

  ```python
  from djust import LiveView
  from djust.components.components import Badge
  from djust.decorators import state


  class ProfileView(LiveView):
      template = "<div dj-root>{{ badge }}</div>"
      badge = state(default_factory=lambda: Badge("new"))  # one per view
  ```

  Keep such a field transient: `persist=` on a component field fails on the
  first event, because a component is not session data. To keep what it shows
  across a reconnect, persist the values instead and build the component from
  them in `get_context_data()`.
- **Embedded children.** Sticky explicit children persist their declared
  fields. Non-sticky explicit children are transient and may not declare
  persisted fields.
- **Tenancy.** The configured tenant resolver is applied to every request,
  including WebSocket mounts, whether or not `TenantMiddleware` is installed.
- **Invalidation.** Change detection is unchanged. After mutating an object in
  place, call `self.set_changed_keys()` (or pass the changed context keys) to
  force a re-render.

## Not supported under explicit exposure

- **Interactive components without a connection.** A component's state lives
  with its view, so on the HTTP fallback, where each request builds a new view,
  a component event is refused. They work over WebSocket and SSE.
- **Time-travel restore and replay.** The timeline is a redacted, read-only
  history.

- **`lazy=True` children.** Refused, with a template error.
- **Components assigned in `mount()`** (`self.nav = Tabs(...)`). Declare a
  `LiveComponent` at class level; hold a plain component as shown under
  Components above.
- **A plain component in the class body** (`badge = Badge(...)`). Refused when
  the class is created; see Components above.
- **`{% dj_activity %}`, `{% colocated_hook %}`, `{% live_form %}`,
  `{% live_field %}` and `{% live_errors %}` in a root template.** The Rust
  renderer has no handler for these tags under either policy; they work only in
  templates rendered with Django's engine.

## Keeping the legacy policy

A view written for djust 1.x relies on the attribute walk: its template reads
attributes that nothing supplies, and under the default they render empty,
without an error. Until such a view is migrated, state the old policy on it:

```python
class OldView(LiveView):
    exposure_policy = "legacy"
```

To keep a whole project on it for now, state it once on a base class your views
inherit from. There is no setting that changes the default.

Stating `"legacy"` raises a `DeprecationWarning` when the class is defined, once
for each class that states it. The policy is removed in djust 2.0: the 2.0
pre-releases still accept it, and 2.0.0 does not. The views djust itself ships
that still state it (the admin change list and form,
`TtydTerminalView`, the `@live_view` decorator) do not warn, since that line is
not yours to change.

## Migrating a view

1. State `exposure_policy = "legacy"` on the view (above), so it keeps working
   while you change it.
2. Run `python manage.py djust_exposure_inventory --view myapp.views.MyView`.
   It lists the names legacy mode exports and where each one goes, and suggests
   declarations. It never prints values and never instantiates the view.
3. Declare the fields you meant to keep with `state()`, choosing `persist` and
   `client` for each one deliberately. A declared field renders without
   anything else.
4. Return everything else the template reads (derived values, `@computed`
   values, models loaded from a stored id) from `get_context_data()`.
5. Remove the `exposure_policy` line, then test your reconnect and
   back-navigation flows. A template name that nothing supplies renders empty,
   so look at every page the view renders. Two things point at such a name:
   with `DEBUG = True` djust logs a warning (logger `djust.exposure`) the first
   time a view renders a name its template reads and the view sets but does not
   supply, and `python manage.py check` reports it as `djust.T027`. Neither
   reports a declared field that rendered; both still name one declared
   `render=False`, or one dropped by a `get_context_data()` that does not build
   on `super()`.

**Cost.** An explicit WebSocket event costs roughly twice a legacy one, because
of the per-turn authorization and the state save. The measured numbers and
their variance are in the ADR's
[cost note](https://github.com/djust-org/djust/blob/main/docs/adr/notes/038-cost-measurement.md).
