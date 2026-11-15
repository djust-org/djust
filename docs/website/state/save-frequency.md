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
- a turn whose handler wrote `request.session` itself, so a plain Django view
  reading the session sees the value;
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

A save that runs after its turn cannot refuse that turn. When storage fails,
the change stays live in the view, the save is tried again with a growing
delay, and a WARNING is logged after three failures in a row. When the state
is over its size limit (`DJUST_AUTOMATIC_STATE_MAX_KB`), the store keeps the
last state that fit, the view keeps its values, and the page receives one
`state_too_large` error frame (with `source: "async"`); later saves try again,
so a state that shrinks is saved again. A reconnect restores the last state
that fit.

## Another connection for the same tab

Each tab's saved state names the connection or request that holds it (a
holder epoch). With an interval set, a reconnect or an HTTP fallback request
that restores the tab claims it, and a connection that still holds older
changes for that tab (a socket whose client has gone away unnoticed) can no
longer write over them: its later saves are refused and its turns answer with
the reload error.

## What it does not cover yet

- The page is not yet told when a reconnect restored an older state than it
  showed (the "recent changes could not be restored" notice).
- daphne 4 cancels the application on SIGTERM without delivering the socket
  close and runs no `lifespan`: a graceful stop there loses up to the interval.
- A value that cannot be saved is reported when a save meets it; with an
  interval set, a value held only between two saves is never reported.

## Settings

| Setting | Meaning |
|---------|---------|
| `DJUST_STATE_SAVE_INTERVAL` | Seconds. Over a connection, state is saved no later than this after a change. `0` (default): before every reply. |
| `state_save_interval` (view attribute) | The same for one view; `None`: the project's value. |
