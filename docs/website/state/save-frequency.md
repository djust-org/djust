# How often view state is saved: `DJUST_STATE_SAVE_INTERVAL`

By default every WebSocket and SSE turn saves the view's state before its
reply: the browser never shows a state storage does not have. Saving is a
large part of a turn's cost, though, and over a connection the view stays in
the server's memory between turns, so it does not have to be read back from
storage. `DJUST_STATE_SAVE_INTERVAL` lets those turns answer first and save
later.

```python
# settings.py
DJUST_STATE_SAVE_INTERVAL = 5   # seconds; 0 (the default) saves before every reply
```

## What it guarantees

Over a WebSocket or SSE connection, what a handler sets is live at once and
reaches storage

- no later than the interval after the first unsaved change (one timer per
  connection, armed by the first change and never pushed back);
- when the connection closes, before the view's `disconnected()` hook;
- before the view is replaced by a navigation (`live_redirect`, an SSE
  `live_redirect_mount`, a view beside the page view being unmounted);
- with any later turn that is saved before its reply (below);
- when the ASGI `lifespan` shutdown runs (`djust.lifespan.with_lifespan`).

If the process is killed (SIGKILL, an out-of-memory kill, a lost host)
between saves, the reconnect restores the last save: at most the interval
behind what the user saw. A graceful stop under uvicorn loses nothing: on
SIGTERM uvicorn closes each socket, and the close saves.

The HTTP fallback is unchanged: each request rebuilds the view from storage
and saves before it answers.

## Turns that are always saved before their reply

- every turn of a view with a declared `persist="server"` or
  `persist="client"` field;
- every turn when `DJUST_STATE_STORE_STRICT = True` (strict refuses a turn
  whose state cannot be stored, which needs the save before the reply;
  system check `djust.C041` says so);
- a URL change (`url_change`, `live_patch`) and a turn that queues navigation;
- a turn whose handler wrote `request.session` itself: the session write is
  stored with that turn's save, before the reply, when the turn's save writes
  (it changed the view's state, or an earlier deferred turn did). A turn that
  changes only `request.session` and no view state writes nothing on the
  socket path, with or without an interval (a separate issue);
- a handler marked `@event_handler(save=True)`, and any turn that calls
  `self.save_state()` (from a handler, a background callback or
  `handle_tick()`; a component's `save_state()` saves its view).

```python
from djust.decorators import event_handler

class CheckoutView(LiveView):
    @event_handler(save=True)          # in storage before the page shows it
    def place_order(self, **kwargs):
        ...

    @event_handler()
    def pick_step(self, step: int = 1, **kwargs):
        self.step = step
        if step == 3:
            self.save_state()          # only this turn
```

## One view

A view's `state_save_interval` overrides the project's setting for that view;
`None` (the default) takes the project's value, `0` saves before every reply.

```python
class Dashboard(LiveView):
    state_save_interval = 10
```

The interval is `0` or a number of seconds from 0.5 to 60, at most half of
`DJUST_SERVER_STATE_MAX_AGE` (a live page's stored state is rewritten at half
its lifetime). Anything else is read as `0`, with a WARNING, and system check
`djust.C040` reports it at startup.

## When storage fails, or the state is too large

A save that runs after its turn cannot refuse that turn. When storage fails
(including the session read that authorizes the save), the change stays live
in the view, the save is tried again with a growing delay, and a WARNING is
logged after three failures in a row; the page is not told. Only a refusal
(the session logged out, the user changed, a permission revoked) drops the
unsaved change, and only when it repeats three times in a row (or at a
close, which cannot try again): Django's `cache` session engine reads a cache
error as an empty session, which looks exactly like a logout, so one refusal
alone is not trusted. A real logout is retried twice, writing nothing.

When the state is over its size limit (`DJUST_AUTOMATIC_STATE_MAX_KB`), on a
deferred or an immediate turn alike, the turn is served, the store keeps the
last state that fit and the view keeps its values. The page receives a
`state_too_large` error frame (with `source: "async"`): once per field and
limit for saves made after a turn, and every time for a turn that asked for
its save (`save=True`, `save_state()`, a session write, a URL change), whose
`request.session` writes are stored all the same. Later saves try again, so a
state that shrinks is saved again. A reconnect restores the last state that
fit.

At the ASGI `lifespan` shutdown every unsaved view is saved at once, the
whole under one deadline (5 seconds); a WARNING counts what did not finish,
and those views restore their last save. The deadline bounds djust's flush,
not the store: a save that was abandoned keeps its worker thread until the
store answers.

## Another connection for the same tab

With an interval set, every socket mount of a tab (whether it restores saved
state or starts from `mount()`) and every HTTP fallback request for it
records a new holder token for the tab in the session. A socket's save is
refused only when ANOTHER, unexpired token is recorded: a connection that
holds older changes for that tab (a socket whose client has gone away
unnoticed) cannot write over the newer holder's, even when the tab's saved
state was evicted, deleted or expired in between. When its save is refused,
the page is sent the reload error at once, and its later turns get it too.
When no other token is recorded (the record was wiped by a plain Django
request saving an older copy of the session, dropped as the oldest of more
than 32 in the session, or expired after the tab sat idle), the socket is
the holder: it saves and records its token again. HTTP fallback requests
write within their own request, as before: two that overlap for one tab are
not refused, and the later save wins.

What it does not catch:

- A takeover and a wipe of the record inside the same interval: the older
  socket's next save is accepted. This needs another connection to take the
  tab and a plain request to save an older copy of the whole session before
  the older socket flushes; the wipe usually removed the newer state too.
- On `cache`, `file`, and `db` on SQLite there is no row lock, so the check
  and the write are two steps. A writer that pauses between its read of the
  session and its write can land after a newer holder took the tab, and
  can even put its own token back, so that the NEWER holder is refused next.
  With real threads racing 300 times per engine this happened (roughly, it
  varies by run): `cache` 0, `file` 9 to 22, `cached_db` 42 to 46, `db` on
  SQLite 32 to 38. On `db` and `cached_db` with a database that locks rows
  (PostgreSQL, MySQL) the check runs under the row lock.

## What it does not cover yet

- The page is not yet told when a reconnect restored an older state than it
  showed (the "recent changes could not be restored" notice), nor when its
  saves keep failing.
- daphne 4 cancels the application on SIGTERM without delivering the socket
  close and runs no `lifespan`: a graceful stop there loses up to the interval.
- A value that cannot be saved is reported when a save meets it; with an
  interval set, a value held only between two saves is never reported.

## Settings

| Setting | Meaning |
|---------|---------|
| `DJUST_STATE_SAVE_INTERVAL` | Seconds. Over a connection, state is saved no later than this after a change. `0` (default): before every reply. |
| `state_save_interval` (view attribute) | The same for one view; `None`: the project's value. |
