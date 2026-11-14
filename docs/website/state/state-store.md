# Where saved view state is kept: the state store

A view's state is saved between requests so a reconnect, a page-POST fallback
request or another process can pick it up (see
[What Survives a Reconnect, a Restart and a Second Process](../guides/state-survival.md)).
By default it rests in the Django session, readable by whoever can read the
session store. One setting moves it somewhere else.

```python
# settings.py
DJUST_STATE_STORE = "djust.state_store.SealedSessionViewStateStore"
DJUST_STATE_STORE_KEYS = [os.environ["DJUST_STATE_KEY"]]
```

With no `DJUST_STATE_STORE` nothing changes: the state is in the session, laid
out exactly as before.

## What a store does, and what it cannot do

A store only moves bytes under a key djust builds. djust keeps the state
envelope and every check it makes on it: the binding to the session, user,
tenant, route and tab, the expiry, the size limits, and which names may be put
back on the view. Whatever a store returns goes through all of those checks
again before anything is restored. A store that returns bytes written for
another session, user, tenant, route or tab, an older write, or garbage gives
the view nothing: `mount()` runs, as on a first visit. No store can hand one
caller's state to another.

## What stays in the session

Per tab, one index entry with no values: the store's name, the page, the
binding digest, the tab id, the user and tenant identifiers, when the state
was written and when it expires, its size, and a random stamp naming the one
write the session vouches for. The tab cap (8 per page), the per-view size
limit and the 1 MB session budget work from these entries, so a store never
has to list what it holds.

Fields declared `state(..., persist="server")` do not go through the store in
this version: they stay in their own session entry, in the clear. A project
that must keep all view content out of the session declares no
`persist="server"` fields for now.

One more kind of state has its own road into the session: a lazy child view
(`dj-lazy`) on the deprecated `exposure_policy = "legacy"`, over the page-POST
fallback. It does not go through the store either, so with a store named it is
not saved at all: each fallback request mounts that child again, and one
WARNING per child class says so.

## The built-in sealed store

`djust.state_store.SealedSessionViewStateStore` encrypts the state
(AES-256-GCM) and keeps it in the session entry. No new table, no new service.

```bash
pip install "djust[sealed-state]"
python -c "from djust.state_store import generate_key; print(generate_key())"
```

- **Keys are yours.** `DJUST_STATE_STORE_KEYS` is a list of keys (32 bytes
  each, or their base64 text as `generate_key()` prints it), or a callable
  that returns the list. There is no fallback to `SECRET_KEY`.
- **Rotation.** The first key seals; every key opens. Put the new key first,
  keep the old one for one state lifetime (`DJUST_SERVER_STATE_MAX_AGE`, one
  hour by default), then drop it.
- **Bound to its owner.** The state's key (binding, page and tab) is the
  associated data, so sealed text copied to another session, user, tenant,
  route or tab does not open.
- **Every process needs the same keys**, as every process needs the same
  session store.
- **How often to rotate.** Each save seals with a fresh random 96-bit nonce.
  With random nonces one AES-GCM key should seal well under 2^32 (about four
  billion) messages. At 100 saves a second that is over a year; rotate at
  least yearly, and sooner the busier the site (at 1,000 saves a second,
  every month).
- **Sizes.** The per-view limit and the tabs of one page count the encoded
  state, exactly as without a store, so a view evicts its other tabs at the
  same state sizes. The sealed text in the session is about 1.4 times that
  size, and the session budget (1 MB for all saved view states of a session)
  counts the sealed text, because it bounds what the session store is asked to
  hold: with the sealed store a session keeps fewer states before the oldest
  go. One view at the default 512 KB limit seals to about 700 KB.

System check `djust.C037` reports the sealed store without the `cryptography`
package or without usable keys. Until that is fixed nothing is saved: djust
does not fall back to unsealed state in the session.

## Your own store

Subclass `ViewStateStore` and name it in `DJUST_STATE_STORE`. djust builds one
instance per process.

```python
import time

from djust.state_store import ViewStateStore

class TableStateStore(ViewStateStore):
    def load(self, key):
        row = ViewState.objects.filter(pk=key.id).first()
        return bytes(row.payload) if row else None

    def save(self, key, payload, *, expires):
        ViewState.objects.update_or_create(
            pk=key.id,
            defaults={
                "payload": payload,
                "expires": expires,
                "user": key.user_id,
                "tenant": key.tenant_id,
            },
        )

    def delete(self, keys):
        ViewState.objects.filter(pk__in=[key.id for key in keys]).delete()

    def clear(self, *, expired_only):
        # The sweep: djust deletes a state when it evicts it or `djust clear`
        # removes it, but never on logout or when a session ends. Run
        # `djust clear` on a schedule (or this query) to drop expired rows.
        rows = ViewState.objects.all()
        if expired_only:
            rows = rows.filter(expires__lte=int(time.time()))
        return rows.delete()[0]
```

- `key` is a `StateKey`: `session`, `binding`, `page`, `tab`, `user_id`,
  `tenant_id`, and `id`, which joins binding, page and tab and is unique. Use
  `key.id` as the primary key. `user_id` and `tenant_id` are there for a store
  that scopes a row to its caller (row-level security); their forms are
  `"anonymous"`, `"user:int:5"`, `"none"`, `"tenant:str:acme"`.
- `payload` is opaque bytes. Do not parse or change it.
- `expires` is a Unix time after which djust will not read the state back. Use
  it for a TTL or a cleanup job.
- The store must be reachable from every process, like the session store.
- Optional: `stats() -> dict` and `clear(*, expired_only: bool) -> int`, for
  `djust stats` and `djust clear`.

The three calls are sync and run on the Django thread. Each is a
`"state_store"` step of [`sync_turn_wrapper`](../guides/turn-middleware.md),
so a wrapper that opens a caller-scoped transaction is open around your query.
A wrapper that raises on a step name it does not know must let this one
through. `djust clear` and `djust stats` call the store inside the wrapper as
well, with `view=None` and `turn=None`: a wrapper that scopes a transaction to
the caller must handle that case (an administrative scope, or no scope). If a
store call fails there, the command stops with a non-zero exit and names the
wrapper; `djust clear` deletes a session's states from the store before it
removes their index entries, so a failure leaves that session as it was.

`load()` may return `bytes`, a `bytearray` or a `memoryview` (what a database
driver hands back for a binary column), or `None`.

A save writes the store first and the session's index entry second. If the
process dies between the two, or the session write fails after the store write
succeeded, the store holds the new bytes while the session still vouches for
the previous write. Those bytes are refused (their stamp is not the one the
session holds), so **that tab starts from `mount()` once** on its next request;
its next save puts both back in step. Nothing is left behind: the next save
overwrites the same row.

djust deletes a state from the store when it evicts it (the tab cap, the size
budgets, expiry seen at a write) and when `djust clear` removes it. It never
deletes on logout or when a session ends (`session.flush()`, an expired
session): such rows can no longer be read, and stay until your store's own
`expires` sweep removes them (`clear()` above, run by `djust clear`).

System check `djust.C036` reports a `DJUST_STATE_STORE` that does not import
(whatever importing it raises) or does not name a `ViewStateStore` subclass,
with the dotted path and the error.

## When the store is unreachable

| When | What happens |
|---|---|
| Loading a view's state | The request is served; `mount()` runs. One WARNING per view class. |
| Saving at a page load | The page is served; nothing is saved. One WARNING per view class. |
| Saving after an event | The response is served and an ERROR is logged. The tab's index entry is dropped, so the next request or reconnect starts from `mount()` rather than from an older state. |
| Deleting evicted states | The save goes on; a WARNING is logged. |

`DJUST_STATE_STORE_STRICT = True` refuses the event instead of serving it
unsaved: nothing is written, the last saved state stays, and the client gets
the "State unavailable. Please reload the page." error (`code="state_error"`;
HTTP status 503 on the page-POST fallback). Page loads are served either way.
Only `True` is strict; any other value that is not `False` is a startup error
(system check `djust.C038`) and, on a server that runs no system checks, one
WARNING per process the first time the setting is read.

A log line about a store failure names the view class and the exception's
type. The exception's message is included only where the view allows
exception details in logs.

## Switching stores

After `DJUST_STATE_STORE` changes, entries written for the previous store (or
for the session layout) read as nothing saved: each open page runs `mount()`
once. They do not stay at rest either: the first request, reconnect or page
load of any tab of a session removes every such entry from that session,
including state saved in the clear before the store was named. A lazy legacy
child's entry goes when that child is next reached. Sessions that are never
used again keep theirs until the session expires; `djust clear --all` removes
them now (for the `db` and `cached_db` session engines), and what a previous
store of your own holds is that store's to clear.

## `djust stats` and `djust clear`

Both work from the session index, as before (for the `db` and `cached_db`
session engines, which can list their sessions). `djust clear` deletes from the
store each state it removes from a session, then calls the store's `clear()`
when it has one. `djust stats` prints the store's `stats()` when it has one,
and "Not listed" when it does not.

## Settings

| Setting | Meaning |
|---|---|
| `DJUST_STATE_STORE` | Dotted path of a `ViewStateStore` subclass. Unset: the session. |
| `DJUST_STATE_STORE_KEYS` | The sealed store's keys: the first seals, every one opens. |
| `DJUST_STATE_STORE_STRICT` | `True`: refuse an event whose state cannot be stored. Default `False`. |

`DJUST_AUTOMATIC_STATE = False` (or a view's `automatic_state = False`) is
checked first: with saving off no store is called.
