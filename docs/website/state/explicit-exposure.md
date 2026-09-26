# Explicit exposure

By default a LiveView runs under the `legacy` exposure policy. In that mode every
public attribute you set on `self` becomes template context, and much of it
also ends up in session state and the client state mirror. Explicit exposure
([ADR-038](https://github.com/djust-org/djust/blob/main/docs/adr/038-explicit-context-and-state-exposure.md))
changes that default for the views that opt in. An attribute reaches a
template, the session or the browser only when you say so.

```python
from djust import LiveView, event_handler
from djust.decorators import state


class CounterView(LiveView):
    exposure_policy = "explicit"
    template_name = "counter.html"

    count = state(0, persist="server")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["count"] = self.count
        return context

    @event_handler()
    def increment(self):
        self.count += 1
```

`self.service`, `self.object`, `self.internal_note` and every other ordinary
attribute stay in server memory, available to your handlers and never
exported.

## Where values can go

| Destination | Comes only from |
| --- | --- |
| Template context | `get_context_data()` additions and registered framework providers |
| Server persistence (reconnect, cross-worker restore) | `state(..., persist="server")` fields |
| Raw browser data (`get_state()`, HTTP API `assigns`) | `state(..., client=True)` fields |
| Back-navigation snapshot | `state(..., persist="client", client=True)` fields |
| Debug panel, time travel, bug capture | A redacted projection: names and types, not values |

A value you render is visible in the HTML, as with any Django template. Being
rendered does not also put it in the state mirror, the snapshot or the debug
panel.

## Declaring state

```python
from djust.decorators import state

count = state(0)                                 # reactive, not persisted
cart = state(default_factory=list, persist="server")
page = state(1, persist="client", client=True)   # restorable on back-navigation
```

- `persist="server"` saves the field in a server-side session envelope, bound
  to the session, the user, the tenant and the route.
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
  are rejected.
- **`DJUST_SERVER_STATE_MAX_AGE`.** The lifetime of a server envelope in
  seconds, from 1 to 86400. The default is 3600. System check `djust.C020`
  validates it.
- **`DJUST_EXPLICIT_STATE_SAVE_TIMEOUT`.** How long a turn waits for its
  state save, in seconds: greater than 0 and at most 10. The default is 0.15.
  The time counts from when the save starts running, not from when it is
  queued behind other sessions' work. System check `djust.C024` validates it.
- **`DJUST_STATE_SNAPSHOT_MAX_AGE` / `DJUST_STATE_SNAPSHOT_ENABLED`.** The
  signed back-navigation snapshot's lifetime, and its master switch. The
  service worker also expires stored snapshots after this age.
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
  as a page load does. A socket with no session cookie at all is still refused.
  With `cache` sessions, Django treats a failed cache read as a missing
  session, so a transient cache error on mount also takes this path: that
  socket mounts anonymous while the real session survives.
- **A failed state save is reported, not hidden.** The client gets a
  `state_error` instead of an update, and a failed root save revokes its
  back-navigation snapshot. So the browser never shows a state that storage
  does not have. Saves of one page are ordered: a save that is still running
  delays the next one, so a late write never replaces a newer one.
  A save that only ran out of time is marked `transient` and does not ask the
  user to reload: the change is kept on the server, and once storage answers
  one catch-up update saves it and sends full HTML. If the catch-up cannot do
  that, storage keeps missing the deadline (three deferrals in a row), or a
  save has been running for 10 seconds, the error is the reload error instead.
  At mount there is no page to update yet, so a slow save there is also the
  reload error.
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
  `is_valid` and its messages to the template. None of that is persisted by
  default. To keep a non-sensitive field's input across a reconnect, declare it
  with `form_input = persisted_form_input("name", "email")` from `djust.forms`.
  Opting in a password field, or a name on the sensitive-field list, is
  refused at class definition (at mount, for a form class chosen by
  `get_form_class()`). Such fields render empty and never reach storage,
  frames or debug output.
- **Uploads.** `uploads` renders each entry's progress and a sanitized file
  name. The raw client file name and the writer's result are not included.
  Uploads in flight are not resumed after a reconnect; the client registers
  them again.
- **Components.** Declare components at class level (`nav = Tabs(...)`). Each
  view gets its own instance. Component state is transient: a reconnect
  mounts it fresh.
- **Embedded children.** Sticky explicit children persist their declared
  fields. Non-sticky explicit children are transient and may not declare
  persisted fields.
- **Tenancy.** The configured tenant resolver is applied to every request,
  including WebSocket mounts, whether or not `TenantMiddleware` is installed.
- **Invalidation.** Change detection is unchanged. After mutating an object in
  place, call `self.set_changed_keys()` (or pass the changed context keys) to
  force a re-render.

## Not supported under explicit exposure

- **Actors (`use_actors = True`).** Refused.
- **`lazy=True` children.** Refused, with a template error.
- **Components assigned in `mount()`** (`self.nav = Tabs(...)`). Declare them
  at class level.
- **`{% dj_activity %}`, `{% colocated_hook %}`, `{% live_form %}`,
  `{% live_field %}` and `{% live_errors %}` in a root template.** The Rust
  renderer has no handler for these tags under either policy; they work only in
  templates rendered with Django's engine.

## Migrating a view

1. Run `python manage.py djust_exposure_inventory --view myapp.views.MyView`.
   It lists the names legacy mode exports and where each one goes, and suggests
   declarations. It never prints values and never instantiates the view.
2. Declare the fields you meant to keep with `state()`, choosing `persist` and
   `client` for each one deliberately.
3. Move everything the template reads into `get_context_data()`.
4. Set `exposure_policy = "explicit"`, then test your reconnect and
   back-navigation flows.

**Cost.** An explicit WebSocket event costs roughly twice a legacy one, because
of the per-turn authorization and the state save. The measured numbers and
their variance are in the ADR's
[cost note](https://github.com/djust-org/djust/blob/main/docs/adr/notes/038-cost-measurement.md).
