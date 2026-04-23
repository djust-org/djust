# ADR-009: Mixin Side-Effect Replay on WebSocket State Restoration

**Status**: Accepted
**Date**: 2026-04-22
**Deciders**: Project maintainers
**Related**: [ADR-007](007-package-taxonomy-and-consolidation.md)

---

## Summary

Mixins that register state into **process-wide singletons** during `mount()`
must expose a `_restore_<concept>()` method that replays that registration.
The WebSocket consumer's state-restoration path (which skips `mount()` when
pre-rendered session state exists) calls each `_restore_*` method after
`_restore_private_state()`. This ADR formalizes the pattern first shipped
ad-hoc in PRs [#891](https://github.com/johnrtipton/djust/pull/891) (UploadMixin,
issue #889) and [#895](https://github.com/johnrtipton/djust/pull/895)
(PresenceMixin + NotificationMixin, issues #893 / #894).

## Context

### The state-restoration path skips `mount()`

`python/djust/websocket.py:1540-1572` has a branch where, if the HTTP
handshake produced pre-rendered session state for the view, the WS consumer
restores that state directly instead of re-running `mount()`:

```python
if has_prerendered:
    saved_state = await request.session.aget(view_key, {})
    if saved_state:
        for key, value in saved_state.items():
            safe_setattr(self.view_instance, key, value, allow_private=False)
        private_state = await request.session.aget(f"{view_key}__private", {})
        if private_state:
            await sync_to_async(self.view_instance._restore_private_state)(
                private_state
            )
        # Issues #889, #893, #894 — replay process-wide side effects
        # that mount() would have re-issued.
        if hasattr(self.view_instance, "_restore_upload_configs"):
            await sync_to_async(self.view_instance._restore_upload_configs)()
        if hasattr(self.view_instance, "_restore_presence"):
            await sync_to_async(self.view_instance._restore_presence)()
        if hasattr(self.view_instance, "_restore_listen_channels"):
            await sync_to_async(self.view_instance._restore_listen_channels)()
```

This exists because re-running `mount()` on the WS side is expensive (re-runs
ORM queries, re-hits caches, re-builds state that was already computed on the
HTTP side). Skipping it is a real performance win on pages with heavy mount
logic.

### What the session round-trip preserves and loses

- **Preserved**: public instance attrs (via `__dict__` round-trip) and
  `_private` user attrs (via `_restore_private_state()`). Both paths run
  through `json.dumps` / `json.loads`.
- **Lost**: any side effect `mount()` triggered against a **process-wide
  singleton**. Singletons live in memory, not in the session. Concretely,
  this affects:
  - `PresenceManager` — holds join registrations keyed by presence key.
  - `PostgresNotifyListener` — holds Postgres `LISTEN channel` subscriptions
    on a dedicated `AsyncConnection`.
  - `UploadManager` — per-session upload-slot configs, chunk buffers, temp
    dirs.

The symptom is always the same shape: the public flag attr says "yes I'm
tracking presence / listening to `orders` / have an `avatar` upload slot",
but the manager object doesn't actually know about this view. Requests that
hit the manager fail with "no such configuration" messages.

## Decision

Each mixin that registers process-wide side effects in `mount()` MUST:

1. **Persist the replay input as JSON-serializable attrs.** Whatever state
   the `_restore_*` method reads (e.g. `self._presence_user_id`,
   `self._upload_configs_saved`) MUST round-trip through `json.dumps` /
   `json.loads` unchanged. Live manager instances, connection handles, and
   class references are dropped by `_get_private_state()` and MUST be
   reconstructable from the saved attrs.
2. **Expose a `_restore_<concept>()` method** that replays the side effect
   from those saved attrs. The name is `_restore_<concept>` (not
   `_restore_<mixin>`) so multiple mixins with overlapping concerns stay
   explicit.
3. **Be called by `LiveViewConsumer` state restoration.** The consumer uses
   `hasattr(view, "_restore_<name>")` to invoke each replay method in a
   fixed order, after `_restore_private_state()` and before the component-
   state loop.
4. **Wrap replay in try/except at WARNING level.** Restoration MUST NEVER
   kill the WS. A missing table, a postgres restart, a writer class that
   was renamed in a later djust version — none of those should prevent the
   WS handshake from completing. Log at WARNING, continue to the next mixin.
5. **Be idempotent / convergent.** The replay may fire once per WS connect
   (session-restore path) AND, in some edge cases, run alongside a fresh
   `mount()` on the same process. `ensure_listening`, `join_presence`, and
   `configure` all take "same key" as a no-op / overwrite, so calling the
   replay N times yields the same process state as calling it once.

### Naming and ordering

The consumer calls replay methods in this order:

1. `_restore_upload_configs` — rebuild UploadManager + slot configs.
2. `_restore_presence` — rejoin PresenceManager for this user.
3. `_restore_listen_channels` — re-issue Postgres `LISTEN` per channel.

Order matters only if a later mixin depends on state a prior mixin
restored; today the three are independent. Additions go at the end unless
they have a documented dependency.

### Error handling contract

```python
def _restore_<concept>(self) -> None:
    for item in self._saved_list_of_things:
        try:
            self._replay_one(item)
        except <narrow expected> as exc:
            logger.warning(
                "%sMixin._restore_%s: %s (issue #NNN)",
                type(self).__name__, "<concept>", exc,
            )
        except Exception as exc:  # noqa: BLE001 — restoration must never kill the WS
            logger.warning("...: unexpected error: %s", exc)
```

Narrow-catch for the *expected* failure modes (unsupported backend, schema
change, cross-loop handoff) gets a targeted warning. The `Exception` fallback
is the safety net. No `raise`.

## Concrete examples

### UploadMixin (PR [#891](https://github.com/johnrtipton/djust/pull/891), issue #889)

- Saved attr: `self._upload_configs_saved` — list of dicts, one per
  `allow_upload()` call, each JSON-serializable. `writer=` classes are
  dropped and recorded as a `_had_writer` flag that triggers a WARNING at
  replay time.
- Replay: `_restore_upload_configs()` walks the list, calls
  `self.allow_upload(**cfg)` for each. Rebuilds `self._upload_manager` as a
  side effect of the first call.
- Test: `tests/unit/test_upload_restoration_889.py`.

### PresenceMixin (PR [#895](https://github.com/johnrtipton/djust/pull/895), issue #893)

- Saved attrs: `self._presence_tracked`, `self._presence_user_id`,
  `self._presence_meta`, plus whatever attrs the subclass's
  `presence_key` format string references.
- Replay: `_restore_presence()` calls `PresenceManager.join_presence(...)`
  if `_presence_tracked` is True. `join_presence` is internally idempotent
  on `(key, user_id)`.
- Test: `tests/unit/test_mixin_restoration_893_894.py`.

### NotificationMixin (PR [#895](https://github.com/johnrtipton/djust/pull/895), issue #894)

- Saved attr: `self._listen_channels` — set of channel-name strings.
- Replay: `_restore_listen_channels()` calls
  `PostgresNotifyListener.instance().ensure_listening(channel)` for each.
  `ensure_listening` is idempotent on known channels.
- Cross-loop caveat (issue #896): if the process's listener singleton is
  bound to a different event loop than the one the WS consumer is running
  on, `ensure_listening` raises `RuntimeError` via `_assert_same_loop`.
  The replay catches this, logs a warning, and continues — the listener
  will be reset on the correct loop on the next fresh-mount path.
- Test: `tests/unit/test_mixin_restoration_893_894.py`.

## Alternatives considered

### (A) Don't skip `mount()` on WS restoration

**Rejected** on performance grounds. `mount()` is the user's ORM-query and
cache-fill path; re-running it on every WS reconnect (which can happen on
mobile network flaps) doubles the work the HTTP handshake already did.
Session-state restore exists precisely to avoid this, and users have come
to rely on its perf characteristics.

### (B) Snapshot the entire mixin state (managers, connections, etc.)

**Rejected** on serialization complexity. `UploadManager`, `PresenceManager`,
and `PostgresNotifyListener` hold live resources: file handles, temp dirs,
psycopg connections, asyncio tasks, threading locks. Serializing any of
these is a non-starter. The replay approach accepts that "rebuild from
simple attrs" is the right serialization boundary.

### (C) Make the singletons re-hydrate from the session themselves

**Rejected** on coupling grounds. This inverts the dependency: the
`UploadManager` singleton would have to know about Django sessions. Today
the singletons are pure in-memory process state with no framework knowledge;
keeping them that way preserves their testability.

### (D) Save the live managers to the session via pickle

**Rejected** on security + format-stability grounds. Pickling live manager
objects would (a) require the session backend to accept pickle (many
production setups don't), (b) couple the session format to the manager's
class layout (breaks cross-version restores), and (c) surface the live
boto3 / psycopg / asyncio handles into storage — an attack surface.

## Trade-offs

- **Migration cost when a mixin's constructor signature changes.** A saved
  dict from v0.5.4 might not be replayable in v0.6.0 if `allow_upload`
  gained or renamed a kwarg. Each `_restore_*` method SHOULD wrap its
  per-item replay in try/except and fall back to a minimum-viable replay
  (e.g. `allow_upload(slot_name)` with no other config) when the signature
  mismatches. See the UploadMixin schema-defensive replay (issue #892).
- **Cross-loop handoff.** Mixins that touch asyncio-bound resources (today:
  `PostgresNotifyListener`) must handle the case where the saved state was
  created on a different event loop than the restore path runs on. The
  convention is to detect the mismatch, reset the singleton, and log at
  INFO — never assert.
- **Replay ordering coupling.** The consumer's call order is fixed; a mixin
  that implicitly depends on another mixin's replay having run first is a
  latent bug. We document the order in this ADR and keep replays
  independent when possible.

## Verification

- `python/djust/websocket.py:1558-1571` — the consumer's replay block is the
  canonical integration point.
- `tests/unit/test_upload_restoration_889.py` — end-to-end session
  round-trip for UploadMixin.
- `tests/unit/test_mixin_restoration_893_894.py` — PresenceMixin and
  NotificationMixin.
- `tests/unit/test_mixin_replay_schema_cross_loop_892_896.py` —
  defensive-replay (#892) and cross-loop (#896) regression tests.
