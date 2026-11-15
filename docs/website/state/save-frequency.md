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
unsaved change. When the state is over its size limit
(`DJUST_AUTOMATIC_STATE_MAX_KB`), on a deferred or an immediate turn alike,
the turn is served, the store keeps the last state that fit, the view keeps
its values, and the page receives one `state_too_large` error frame (with
`source: "async"`); later saves try again, so a state that shrinks is saved
again. A reconnect restores the last state that fit.

At the ASGI `lifespan` shutdown every unsaved view is saved at once, the
whole under one deadline (5 seconds); a WARNING counts what did not finish,
and those views restore their last save.

## Another connection for the same tab

With an interval set, every socket mount of a tab (whether it restores saved
state or starts from `mount()`) and every HTTP fallback request for it writes
a new holder record for the tab in the session, and a page load ends the
last holding. A socket writes the tab's state only while the record is
still its own: a connection that holds older changes for that tab (a socket
whose client has gone away unnoticed) can no longer write over the newer
holder's, even when the tab's saved state was evicted, deleted or expired in
between. When its save is refused, the page is sent the reload error at
once, and its later turns get it too. HTTP fallback requests write within
their own request, as before: two that overlap for one tab are not refused,
and the later save wins.

The check is exact on `db` and `cached_db` with a database that locks rows
(PostgreSQL, MySQL): it runs under the row lock. On `cache`, `file`, and
`db` on SQLite there is no row lock: a writer that pauses between its read
of the session and its write (one session read and write, usually
milliseconds) can still land after a newer holder took the tab. That is the
same window every session write has there.

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
