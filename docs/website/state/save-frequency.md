# How often view state is saved: `DJUST_STATE_SAVE_INTERVAL`

Saving a view's state is a large part of a WebSocket or SSE turn's cost
(about a third, [measured](#what-it-saves-a-turn)), and over a connection the
view stays in the server's memory between turns, so it does not have to be
read back from storage. So by default those turns answer first and save
later: no later than `DJUST_STATE_SAVE_INTERVAL` seconds (2 by default) after
the first unsaved change. Set it to `0` to save every turn before its reply,
so the browser never shows a state storage does not have (the 1.x
behaviour).

```python
# settings.py
DJUST_STATE_SAVE_INTERVAL = 2   # seconds, the default; 0 saves before every reply
```

**Why 2 seconds.** The latency gain is the same for any value above 0: the
reply no longer waits for the write. The value decides two other things:
what a killed process can lose (at most the interval of changes) and how
often a busy connection writes (at most once per interval; a user clicking
steadily is saved about every 2 seconds instead of on every click). 2 keeps
the loss on a kill to about one user action and the write rate below one in
2 seconds per connection, and it fits any `DJUST_SERVER_STATE_MAX_AGE` of 4
seconds or more (an interval must be at most half of it; below that the
default is read as 0).

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

## What it saves a turn

Measured with `tests/benchmarks/test_explicit_turn_cost_3997.py` (median
milliseconds per WebSocket turn, 100 rounds, in-process, the `db` session on
SQLite; a shared 4-CPU machine, so read the ratios, not the digits):

| Shape | Saved before the reply | No automatic save | Interval set |
|-------|-----------------------:|------------------:|-------------:|
| counter | 5.2 | 3.5 | 3.4 |
| dashboard | 7.9 | 5.0 | 5.0 |
| table, 500 rows | 26.6 | 17.3 | 18.6 |

With an interval set, a turn costs about what a turn with no automatic save
costs: the save was 30 to 38 per cent of the turn. What remains is the
exposure and render work (#3997 measured the turn without the save; that is
the cost still to cut). The gain is the same for any interval above 0: the
value only decides how much a killed process can lose and how often a busy
connection writes (at most once per interval).

## Turns that are always saved before their reply

- every turn of a view with a declared `persist="server"` or
  `persist="client"` field;
- every turn when `DJUST_STATE_STORE_STRICT = True` (strict refuses a turn
  whose state cannot be stored, which needs the save before the reply;
  system check `djust.C041` says so);
- a URL change (`url_change`, `live_patch`) and a turn that queues navigation;
- a turn whose handler wrote `request.session` itself (an assignment, a
  removal, or an in-place change such as `request.session["cart"].append(x)`):
  the session write is stored before the reply, merged into the session as
  storage holds it, with or without an interval, and also when the turn
  changed no view state. A turn that does not write the session adds no
  session write;
- a handler marked `@event_handler(save=True)`, and any turn that calls
  `self.save_state()` (from a handler, a background callback or
  `handle_tick()`; a component's `save_state()` saves its view).

```python
from djust.decorators import event_handler

class CheckoutView(LiveView):
    @event_handler(save=True)          # in storage before the page shows it
    def place_order(self):
        ...

    @event_handler()
    def pick_step(self, step: int = 1):
        self.step = step
        if step == 3:
            self.save_state()          # only this turn
```

## A handler that sends the browser to another page itself

`live_redirect()` and URL changes are seen as navigation and saved before the
reply. There is no server-side call for a full-page redirect, so a handler that
sends the browser away by pushing its own event (which client code turns into
`window.location = ...`) looks like any other turn: with an interval set it
may be deferred, and the next page can load before the state is stored. Mark
such a handler `save=True`, or call `self.save_state()` on the turn that
leaves; the state is then in storage before the reply, and the push follows
the reply:

```python
class CheckoutView(LiveView):
    @event_handler(save=True)
    def finish(self):
        self.status = "paid"
        self.push_event("navigate", {"url": "/thanks/"})
```

`test_a_handler_that_sends_the_page_away_is_saved_before_its_reply_when_it_says_so`
in `python/djust/tests/test_save_interval_followups_4164.py` pins both, and the
deferred turn without either.

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
state or starts from `mount()`) and every HTTP fallback request for it that
changes the tab's state records a new holder token for the tab in the
session. A fallback request that changes nothing (a client falling back to
HTTP may send one first) renders the stored state and records nothing, so the
socket's later flush still lands. A socket's save is
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
  SQLite 32 to 38. With the writer paused on purpose between its read and its
  write, it lands every time on SQLite (`db` and `cached_db`).
- On `db` and `cached_db` with a database that locks rows, the read is
  `SELECT ... FOR UPDATE` inside the save's transaction, so the newer
  holder's save waits for the late one and is written after it. Measured on
  PostgreSQL 16 with the same paused writer: in the database, 0 late writes
  in 300 rounds, for `db` and for `cached_db`; the newer save waited out the
  pause each time (`python/djust/tests/test_holder_fence_row_lock_4164.py`,
  run with `DJUST_PG_RACE_DATABASE` set). MySQL (InnoDB) takes the same
  `FOR UPDATE` path but has not been run.
- `cached_db` clears its cache entry after the transaction, outside the lock,
  so a slow cache never holds the row. The next read refills the cache from
  the database. Until 2.0 it wrote the merged copy there instead, and a late
  writer's cache write could land after the newer one's and leave the older
  copy in the cache (once in 600 PostgreSQL rounds; every time when the cache
  write was delayed, #4324). A delete is the same in either order.

## When the page is told

Every reply and mount frame of a view whose state is saved carries
`state_serial`, the turn serial of the state it shows: a turn whose save was
deferred shows one past what storage has, a save writes the serial with the
state. A reconnecting page sends its last serial back with its mount frame.

- **The old connection is still open** (the browser moved before the server
  noticed the drop): the reconnect asks it to save first, and waits up to one
  second for storage to catch up. The page sees every change; nothing is
  shown. A connection in the same process is asked directly. One in another
  process (another worker, another host) is asked through the channel layer
  (a WebSocket holder joins its tab's group with its first deferred turn),
  so **with more than one process the channel layer must be shared between
  them** (`channels_redis`). With an in-memory layer, or none, a reconnect
  that lands on another worker restores the last save, up to one interval
  behind, and the old connection's later save is refused because the new one
  took the tab (system check `djust.C046`).
- **Storage is still behind** (the process was killed, or the old connection
  is an SSE stream, which has no channel to ask): the reconnect restores the
  last save and the page gets a transient notice, `state_not_restored`
  ("Your most recent changes could not be restored."), shown in the corner
  status like a deferred save's, and dispatched as `djust:error`.
- **Saves keep failing**: at the third failure in a row the page gets one
  transient `state_error` ("Your recent changes are not saved yet. Saving
  keeps trying."); the values stay live and saving goes on.

## What it does not cover

- Several worker processes with an in-memory channel layer: a reconnect to
  another worker cannot reach the old connection (above); up to one interval
  is lost. Use a shared layer, or `DJUST_STATE_SAVE_INTERVAL = 0`.
- daphne 4 cancels the application on SIGTERM without delivering the socket
  close and runs no `lifespan`: a graceful stop there loses up to the
  interval. See [what a graceful stop keeps, per server](../guides/deployment.md#what-a-graceful-stop-keeps-per-server).
- A value that cannot be saved is reported when a save meets it; with an
  interval set, a value held only between two saves is never reported.
- An HTTP fallback reply carries no serial: a page that falls back to HTTP and
  then reconnects compares against the last socket frame it had.

## Settings

| Setting | Meaning |
|---------|---------|
| `DJUST_STATE_SAVE_INTERVAL` | Seconds, default `2`. Over a connection, state is saved no later than this after a change. `0`: before every reply. |
| `state_save_interval` (view attribute) | The same for one view; `None`: the project's value. |
