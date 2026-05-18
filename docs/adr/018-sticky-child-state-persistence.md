# ADR-018: Sticky-Child LiveView State Persistence Across WS Reconnect

**Status**: Proposed
**Date**: 2026-05-18
**Deciders**: Project maintainers
**Target version**: v1.1.0
**Related**:
- [ADR-011](011-sticky-liveviews.md) — Sticky LiveViews baseline (child registry → preservation → reattach)
- [ADR-014](014-sticky-liveview-autodetect.md) — `{% live_render %}` auto-detection of preserved stickies (tag-driven precedent)
- [`python/djust/mixins/sticky.py`](../../python/djust/mixins/sticky.py) — `StickyChildRegistry`
- [`python/djust/websocket.py`](../../python/djust/websocket.py) — child event routing (line ~2689), the save block (line ~3139), the mount-time restore (line ~1993)
- [`python/djust/mixins/request.py`](../../python/djust/mixins/request.py) — HTTP-path save (`liveview_<path>` write, line ~593)
- [`python/djust/templatetags/live_tags.py`](../../python/djust/templatetags/live_tags.py) — the `{% live_render %}` tag
- Issue [#1471](https://github.com/djust-org/djust/issues/1471) — this ADR's tracking issue
- Issue [#1467](https://github.com/djust-org/djust/issues/1467) — parent investigation (closed Option C)
- PR [#1466](https://github.com/djust-org/djust/pull/1466) — the WS-event save block this work generalizes
- v0.8.6 retrospective ([#1122](https://github.com/djust-org/djust/issues/1122)) — split-foundation pattern

---

## Summary

A djust page can embed two kinds of child LiveView:

1. **LiveComponents** — assigned as parent attributes (`self.foo = MyComponent(...)`), routed by `component_id`. These **already persist** across a WebSocket reconnect: the parent's save path walks `get_context_data()` and writes component state via `_save_components_to_session`.
2. **Sticky-child LiveViews** — full `LiveView` subclasses embedded with `{% live_render %}`, registered on the parent's `StickyChildRegistry` via `_register_child`, routed by `view_id`. These **do not persist**. On a WS reconnect (page refresh, network blip, snapshot/restore), a sticky child's event-driven state is silently lost.

The #1467 investigation confirmed the gap and confirmed it exists on **both** transports — neither the WS save block (`websocket.py:3139`) nor the HTTP save (`mixins/request.py:~593`) writes sticky-child state.

The SAVE side is mechanically simple. The hard part is the **LOAD side**: a sticky child does not exist at the parent's `mount()` time — it is constructed during template render by the `{% live_render %}` tag. At reconnect-mount the parent has not rendered yet, so it does not yet know which children it has.

This ADR proposes: persist each sticky child under a **stable** session key derived from its `sticky_id`, and **restore it inside the `{% live_render %}` tag at render time** — mirroring the tag-driven design ADR-014 already established for sticky reattach, and the parent's own "skip `mount()` when saved state exists" restore path.

## Context

### What persists today, and what doesn't

The parent LiveView's own state persists through a save/restore pair:

- **Save** — `websocket.py:3139`, inside `handle_event`, after a handler runs:
  ```python
  if target_view is self.view_instance and getattr(
      self.view_instance, "enable_state_snapshot", False
  ):
      # writes liveview_<path>, liveview_<path>__private,
      #        liveview_<path>_components
  ```
- **Restore** — `websocket.py:1993`, inside `handle_mount`:
  ```python
  view_key = f"liveview_{page_url}"
  saved_state = await request.session.aget(view_key, {})
  if has_prerendered or saved_state:
      # safe_setattr public attrs; _restore_private_state;
      # restore components; skip mount()'s state-init
  ```

The save-block gate `target_view is self.view_instance` is the bug. When a sticky-child event fires, the routing path (`websocket.py:2689`) sets `target_view = all_children.get(view_id)` — `target_view` is now the **child**, not `self.view_instance`, so the save block is skipped entirely. The child's state is never written. On the next mount, the child is re-constructed and re-`mount()`ed from scratch.

### The discovery problem

Persisting the child is not enough — the LOAD side has a structural ordering problem:

- A sticky child is created during the parent's template render, when `{% live_render %}` runs. It does **not** exist during the parent's `mount()`.
- At reconnect, `handle_mount` runs the parent's `mount()` (or restores the parent) and then renders. Only *during* that render are the children constructed.
- So there is no point "after mount, before render" at which the parent can iterate a known set of children and restore them — the children do not exist yet.

The restore therefore has to happen **at the moment each child is constructed** — i.e. inside the `{% live_render %}` tag — or be driven by an index written by a previous render.

### `_view_id` is not a stable key

`StickyChildRegistry._assign_view_id` returns the caller's `preferred` id if given, otherwise a process-global monotonic stamp `child_{N}` (`itertools.count`). The `child_N` form is **not stable**: it depends on instantiation order and resets on process restart. It cannot be a session key — the same child would be `child_3` in one process and `child_8` in another.

The stable identifier for a sticky child is its **`sticky_id`** class attribute (the same id ADR-011/014 use for reattach). Persistence must key on `sticky_id`, which means **only children with a stable id are persistable**. Auto-`child_N` non-sticky embedded children are out of scope (see Decision 1).

### Why now

#1467 (v0.9.7-3) investigated a downstream report, found the gap, and closed Option C — out of scope for that bugfix, needs a design pass. #1471 was filed to track the design + implementation. It is staged for v1.1.0. It is not a 1.0 release blocker (sticky children are an advanced embedding pattern; the common case — a single top-level LiveView — already persists), so it rides in the post-1.0 minor.

## Decision

### Decision 1: Persist keyed on the stable `sticky_id`, not the volatile `_view_id`

The per-child session key is:

```
liveview_<parent_path>__sticky__<sticky_id>            # public state
liveview_<parent_path>__sticky__<sticky_id>__private   # private state
```

- `<parent_path>` namespaces by the embedding parent's request path. A sticky child class may be embedded under different parents on different routes; path-namespacing keeps each embedding's state distinct, and matches how the parent's own `liveview_<path>` key is formed.
- `<sticky_id>` is the child's `sticky_id` class attribute — stable across processes and reconnects.

**Only children with a stable `sticky_id` are persistable.** A `{% live_render %}` embed that is *not* `sticky=True` (auto-`child_N` id) is **not** persisted — its `child_N` id cannot survive a process boundary. This is a documented limitation, not a bug: non-sticky embeds are by definition not expected to outlive a navigation. Apps that need a non-sticky embed's state to survive reconnect must give it `sticky=True` + a `sticky_id`.

### Decision 2: Restore inside the `{% live_render %}` tag, at render time

When `{% live_render sticky=True %}` constructs a sticky child during render, the tag — before calling the child's `mount()` — checks the session for `liveview_<parent_path>__sticky__<sticky_id>`. If saved state exists **and** the snapshot-restore conditions hold (Decision 5), the tag applies the saved public + private state to the child **in lieu of** the child's `mount()` state-init, exactly as the parent's restore path skips `mount()` when `saved_state` is present (`websocket.py:1993`).

This mirrors ADR-014's core decision — *the tag is the right place*. ADR-014 explicitly rejected a consumer-side post-render scan because it wastes the fresh mount and orphans `mount()` side effects. The same reasoning applies: restoring after the child has already `mount()`ed would double the work and fire `mount()` side effects (`start_async`, presence join) that then have to be unwound.

**Rejected: mount-time index iteration.** The alternative — parent writes an index of child ids, reconnect-mount reads it and iterates — cannot work cleanly: the children do not exist at mount time (the discovery problem). It would require either constructing children outside the render path (a second instantiation path to maintain) or a post-render restore-then-re-render pass (double render). Tag-driven restore needs neither.

### Decision 3: The sticky-id index is a GC ledger, not the restore driver

The parent's render path writes one extra key:

```
liveview_<parent_path>__sticky_ids = [<sticky_id>, ...]
```

— the set of sticky `sticky_id`s rendered in the most recent render cycle. Its purpose is **garbage collection, not restore**: it lets the save path detect session entries for children that are no longer rendered (a child removed from the template, or behind a now-false `{% if %}`) and prune them, so `liveview_<path>__sticky__*` entries do not accumulate unbounded in the session store.

The happy-path restore (Decision 2) does **not** read the index — each tag invocation self-restores. The index is a secondary correctness aid. Cost: one extra `session.aset` per render of a parent that has sticky children.

### Decision 4: SAVE side — generalize the save-block gate

The `websocket.py:3139` gate becomes two branches:

- `target_view is self.view_instance` → save the parent (current behavior, unchanged).
- `target_view is not self.view_instance` AND `target_view` has a truthy `sticky_id` AND the opt-in conditions of Decision 5 hold → save the child to `liveview_<parent_path>__sticky__<sticky_id>` (+ `__private`), using the same private-first / public-via-`get_context_data()` ordering and the same `asyncio.wait_for` time-bound the parent save already uses.

The identical generalization is applied to the HTTP-path save (`mixins/request.py:~593`) so both transports persist sticky children consistently — the #1467 investigation confirmed the gap is transport-symmetric.

`<parent_path>` for the child is the **parent's** mount-request path (`_djust_mount_request.path`), reached via the child's `_parent_view` back-pointer (set by `_register_child`).

### Decision 5: Opt-in — child persistence requires BOTH child and parent `enable_state_snapshot`

A sticky child is persisted only when **both**:

- the child class has `enable_state_snapshot = True`, and
- its parent class has `enable_state_snapshot = True`.

**Why require the parent too?** Reconnect restore must be tree-consistent. If a child restored to its saved state while its parent re-`mount()`ed fresh (parent not opted in), the parent and child would diverge — the parent renders at default state, the child at saved state, and any parent→child prop the child read at `mount()` is now stale. Requiring parent opt-in guarantees the whole subtree restores together or not at all.

A child that opts in under a parent that does not is a misconfiguration. Surface it:

- A `djust check` system check (category `V`) flags `enable_state_snapshot = True` on a class used as a sticky child whose embedding parent(s) do not opt in.
- At runtime, the tag emits a `logger.warning` once per `(parent, sticky_id)` when it skips a child save for this reason.

### Decision 6: Restore invalidates framework caches, re-runs side-effect replay

A restored sticky child takes the same `_restore_*` replay path the parent already uses (`websocket.py:1996-2023`): `_restore_private_state`, `_restore_upload_configs`, `_restore_presence`, `_restore_listen_channels` — whichever the child mixes in. Framework slots that are not user state (e.g. ADR-017's `_object` cache) are left at their `__init__` default so they re-derive fresh, which correctly handles "the underlying object changed while the client was disconnected." The child's `mount()` state-init is skipped (Decision 2); its non-state side effects that are not covered by a `_restore_*` method are the child author's responsibility, documented in the guide.

## Iteration plan (split-foundation)

Per Action #1122, this is a multi-phase change touching the save path, the template tag, and a system check. **Three iterations**, each soaking before the next:

### v1.1.0 — iter 18a — SAVE side + key scheme (foundation)

- Stable-key scheme (Decision 1): `liveview_<path>__sticky__<sticky_id>` helpers.
- Generalize the WS save-block gate and the HTTP save (Decision 4).
- Write the sticky-id index on render (Decision 3) + prune orphaned entries.
- Regression suite: child event → child state written under the stable key; non-sticky embed NOT written; index reflects the rendered set; orphaned entry pruned.
- **Soak through one release.** The save semantics are the foundation; restore builds on them.

### v1.1.0 — iter 18b — LOAD side (tag-driven restore)

- `{% live_render %}` tag restores saved child state in lieu of `mount()` state-init (Decision 2).
- `_restore_*` replay for the child (Decision 6).
- Regression suite: reconnect restores child public + private state; child `mount()` state-init skipped when saved state present; round-trip (set state → reconnect → assert restored) on both WS and HTTP paths.

### v1.1.0 — iter 18c — opt-in enforcement + docs

- `djust check` system check for the child-opts-in / parent-doesn't misconfiguration (Decision 5).
- Runtime one-shot warning.
- Guide section in `docs/website/guides/` covering the opt-in contract, the stable-`sticky_id` requirement, and the non-sticky-embed limitation.

## Consequences

### Pros

- **Closes the gap symmetrically.** Sticky children persist on both WS and HTTP, like LiveComponents already do.
- **Consistent with existing design.** Tag-driven restore reuses ADR-014's established pattern and the parent's own skip-`mount()`-on-saved-state path — no new restore mechanism.
- **Opt-in, zero-cost when unused.** Apps with no sticky children, or no `enable_state_snapshot`, see no behavior change and no extra `session` writes.
- **Bounded session growth.** The index-driven GC sweep keeps `liveview_<path>__sticky__*` entries from accumulating.

### Cons

- **New session keys are public-ish surface.** The `liveview_<path>__sticky__<id>` scheme becomes a compatibility contract once it lands in a release.
- **Stable-id requirement.** Only `sticky=True` embeds persist; non-sticky `child_N` embeds cannot. Apps wanting persistence must adopt `sticky_id`.
- **Per-render index write.** One extra `session.aset` per render of a parent with sticky children.

### Risks

- **`get_context_data()` cost on the child save path.** The child save calls the child's `get_context_data()`; a heavy override runs on every child event. Mitigated by the same `asyncio.wait_for` time-bound the parent save uses.
- **Parent/child opt-in drift.** A child opts in, a parent later removes `enable_state_snapshot` — silent partial persistence. Mitigated by the Decision 5 system check + runtime warning.
- **Nested stickies** (a sticky child that itself embeds sticky children) — the path-namespacing is by the *immediate* parent; a grandparent reconnect would need recursive restore. Out of scope for v1 (see below); the design does not preclude a later recursive pass.
- **Session-store size.** Many sticky children × large state = large session rows. The GC ledger bounds *count*; per-child state size remains the app author's responsibility, same as the parent's own snapshot.

## Alternatives considered

### Alt 1: Mount-time index iteration (the issue's original Phase A/C framing)

Parent writes a child-id index; reconnect-mount reads it and reconstructs each child. **Rejected as the restore driver** — the discovery problem means children don't exist at mount time, so this needs either a second child-construction path or a restore-then-re-render double pass. The index is kept, but demoted to a GC ledger (Decision 3).

### Alt 2: Persist children inside the parent's snapshot (like LiveComponents)

Walk the registry in the parent's save, nest child state inside `liveview_<path>`. **Rejected** — LiveComponents can do this because they are parent *attributes* present at `get_context_data()` time; sticky children are render-created and not reachable from the parent's context dict. Nesting would also couple child state lifetime to the parent's snapshot and complicate per-child GC.

### Alt 3: Key on `_view_id` (the issue's literal proposal)

The issue text proposed `liveview_<parent_path>__<view_id>`. **Rejected** — `_view_id` is the volatile `child_N` stamp for non-pinned embeds (Context § "`_view_id` is not a stable key"). Keying on `sticky_id` is the corrected form.

### Alt 4: Client-side child state stash

Have the client stash sticky-child state (like the DOM `stickyStash`) and replay it on reconnect. **Rejected** — server-authoritative state must not round-trip through the client (tampering surface); and it would not cover the server-initiated reconnect / snapshot-restore path.

## Out of scope

- **Nested sticky-within-sticky** recursive restore — single-level only for v1.
- **Non-sticky (`child_N`) embedded-child persistence** — no stable key; explicitly unsupported.
- **Cross-process / multi-worker sticky maps** — persistence rides on the configured Django session backend; if that is Redis/DB-backed it already works cross-process, and if it is in-memory it does not. No new cross-process registry is introduced.
- **HTTP hard-reload preservation beyond session state** (Service Worker territory) — same boundary ADR-014 drew.

## Acceptance

- [ ] Stable-key helpers (`liveview_<path>__sticky__<sticky_id>` + `__private`) implemented (18a).
- [ ] WS save-block gate generalized to save sticky children; HTTP save path matched (18a).
- [ ] Sticky-id index written per render; orphaned entries pruned (18a).
- [ ] `{% live_render %}` tag restores saved child state in lieu of `mount()` state-init (18b).
- [ ] Child `_restore_*` replay runs on restore (18b).
- [ ] `djust check` flags child-opts-in / parent-doesn't (18c).
- [ ] Guide section published: opt-in contract, stable-`sticky_id` requirement, non-sticky limitation (18c).
- [ ] Round-trip regression (set child state → reconnect → assert restored) green on both WS and HTTP paths.
- [ ] No behavior change for pages without sticky children, or without `enable_state_snapshot` — verified by running the existing demo + djust.org suites unchanged.

## References

- Issue [#1471](https://github.com/djust-org/djust/issues/1471) — tracking issue
- Issue [#1467](https://github.com/djust-org/djust/issues/1467) — parent investigation (Option C close)
- PR [#1466](https://github.com/djust-org/djust/pull/1466) — the WS-event save block generalized here
- ADR-011, ADR-014 — Sticky LiveViews baseline + tag-driven autodetect precedent
- ADR-017 — `_object` framework-slot-vs-user-state precedent (Decision 6)
- v0.8.6 retro / Action #1122 — split-foundation pattern
- ROADMAP.md milestone v1.1.0
