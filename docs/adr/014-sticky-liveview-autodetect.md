# ADR-014: `{% live_render %}` Auto-Detection of Preserved Stickies

**Status**: Proposed
**Date**: 2026-04-26
**Deciders**: Project maintainers
**Target version**: v0.9.0 (P1, 1.0-blocker)
**Related**: [ADR-011](011-sticky-liveviews.md) (Sticky LiveViews baseline),
[`python/djust/templatetags/live_tags.py`](../../python/djust/templatetags/live_tags.py) `live_render` tag,
[`python/djust/websocket.py`](../../python/djust/websocket.py) `handle_live_redirect_mount`/`handle_mount`,
[`python/djust/static/djust/src/45-child-view.js`](../../python/djust/static/djust/src/45-child-view.js) client stash + reattach,
Issue [#1032](https://github.com/djust-org/djust/issues/1032),
v0.8.6 retrospective ([#1122](https://github.com/djust-org/djust/issues/1122)) — split-foundation pattern

---

## Summary

ADR-011 shipped Sticky LiveViews end-to-end through a three-phase rollout
(child registry → preservation → reattach). It works for the canonical
"app shell" path: Dashboard (instantiates) → Settings (slot only) →
Reports (slot only). It does **not** work for the natural "return-trip"
case: Dashboard → Settings → Dashboard. The destination Dashboard's
template re-runs `{% live_render "AudioPlayer" sticky=True %}`,
the tag freshly instantiates and registers a child under
`sticky_id="audio-player"`, and the consumer's post-render survivor
re-register hits `ValueError` and discards the preserved sticky via
`_on_sticky_unmount()`. The user's audio playback dies on a
Dashboard→Dashboard navigation.

This ADR teaches `{% live_render ... sticky=True %}` to detect, at
template-render time, whether the consumer is currently holding a
preserved instance of the same `sticky_id`. When it is, the tag emits
a `<div dj-sticky-slot="<id>">` placeholder marker — exactly the same
shape Settings/Reports declare manually — and skips the fresh mount.
The consumer's existing slot scan + reattach picks up the placeholder,
re-registers the preserved child onto the new parent, and the client's
`reattachStickyAfterMount` swaps the placeholder for the stashed DOM
subtree. Result: Dashboard → Dashboard preserves audio identity, just
like Dashboard → Settings does today.

## Context

### What works today (ADR-011)

Sticky preservation runs through the WS `live_redirect_mount` pipeline:

1. Client receives `live_redirect`. Detaches every `[dj-sticky-view]`
   subtree into `stickyStash` (`45-child-view.js`).
2. Client sends `live_redirect_mount` to the server.
3. Consumer (`websocket.py` `handle_live_redirect_mount`) calls
   `old_view._preserve_sticky_children(new_request)` to filter sticky
   children that survive auth re-check on the new URL. Survivors are
   stashed on `consumer._sticky_preserved` keyed by `sticky_id`.
4. Consumer tears down the old view, calls `handle_mount(...)` for the
   new view, passing `sticky_preserved` through.
5. New view instantiates; consumer wires `view._ws_consumer = self`.
6. New view's template renders. Today, every
   `{% live_render ... sticky=True %}` in the template freshly mounts
   the child and registers it under its `sticky_id`.
7. After render, `handle_mount` scans the rendered HTML for
   `[dj-sticky-slot="<id>"]` markers and tries to re-register each
   survivor onto the new parent.
8. Consumer emits a `sticky_hold` frame listing the final survivor IDs
   BEFORE the `mount` frame, so the client's `reconcileStickyHold`
   runs before `reattachStickyAfterMount`.
9. Client receives `mount`, applies the new HTML, then walks
   `[dj-sticky-slot]` and `replaceWith()`s each slot using the matching
   stash entry — DOM identity, scroll, focus, form values, and the
   running `<audio>` element all preserved.

### Where the Dashboard→Dashboard case breaks

Step 6 is the failure point. When the destination template re-issues
`{% live_render "AudioPlayer" sticky=True %}`, the tag:

* Calls `child_cls()` to create a *fresh* `AudioPlayerView` instance.
* Calls `parent._register_child("audio-player", new_child)`. The
  registry is empty at this point (it's a brand-new parent), so this
  succeeds.
* Calls the new child's `mount()`, runs `get_context_data`, renders
  the child template, and emits the wrapping
  `<div dj-view dj-sticky-view="audio-player" dj-sticky-root>...</div>`.

In step 7, the consumer scans the just-rendered HTML for
`[dj-sticky-slot="audio-player"]`. There is no slot — only a
`dj-sticky-view`. The slot scan reports no match, and the survivor
falls into the "no slot" branch, which calls `_on_sticky_unmount()`
and drops the preserved instance.

Even if we taught the slot scan to also accept `dj-sticky-view` as a
match, we'd then have two registrations for the same `sticky_id`
(the freshly-mounted one + the survivor), which would `ValueError` on
the second `_register_child` call.

The bug is therefore in the **template tag**, not in the consumer's
slot scan. The tag must avoid creating a fresh child when a survivor
exists, and emit a slot placeholder instead so the existing reattach
path can do its job.

### Why this needed to be reopened (Stage 4 review history)

Issue #1032 was first triaged as part of the v0.8.x sticky workstream,
deferred to v0.9.0 on 2026-04-25 with the rationale "isn't a 1-PR
refactor". The triage was correct in shape (this needs an ADR-locked
design before code) but underestimated the size of the actual diff once
the design is fixed:

* The transport mechanism is **already in the WS pipeline**; no new
  protocol surface, no cookie, no header, no cross-process registry.
* The tag-side change is a single conditional branch: "if a survivor
  exists for `sticky_id`, emit slot; else emit subtree." ~30 LoC.
* The demo template change is a one-line comment update.
* Tests: tag unit tests + integration tests through the existing
  `_FakeConsumer` rig + JS test for two consecutive `replaceWith`
  cycles.

This is a 1-PR feature **iff** the design is locked to "consult
`consumer._sticky_preserved` from the tag." The split-foundation rule
(retro #1122) applies when there is a high-blast-radius foundation
needing to ship before a capability builds on it. Here, the foundation
(WS sticky pipeline) is already shipped and stable — this is a pure
capability layer on top.

## Decision

**The `{% live_render ... sticky=True %}` tag consults
`parent._ws_consumer._sticky_preserved` at render time. When a survivor
exists for the resolved `sticky_id`, the tag emits a slot placeholder
(`<div dj-sticky-slot="<id>"></div>`) and skips fresh mount.
Otherwise, it falls through to the existing fresh-mount path
unchanged.**

### Detailed contract

1. **Resolution order inside the tag** (Phase B sticky branch):
   1. Validate `child_cls.sticky` and `child_cls.sticky_id` (existing).
   2. Enforce `sticky_id` uniqueness in the current render
      (`_STICKY_IDS_SEEN_KEY`) (existing).
   3. **NEW**: Look up `parent._ws_consumer` (may be `None` on HTTP
      GET path or in tests using a non-WS render context).
   4. **NEW**: If `_ws_consumer` exists and
      `_ws_consumer._sticky_preserved.get(sticky_id)` is the surviving
      child instance:
      * Re-register the survivor under `sticky_id` on the new parent
        via `parent._register_child(sticky_id, survivor)`.
      * Update `survivor.request = parent.request` so handlers see the
        new request. **Load-bearing**: the staging-time request from
        `_preserve_sticky_children` is a different object than the
        mount-time request on the new parent — middleware on the new
        request may have set session/user attributes the survivor's
        handlers will read.
      * Add `sticky_id` to `consumer._sticky_auto_reattached` so the
        consumer's post-render slot scan does NOT try to re-register
        the same survivor a second time.
      * Return the slot placeholder
        `<div dj-sticky-slot="<id>"></div>`.
   5. **ELSE** (no survivor, or no consumer back-reference): existing
      fresh-mount + `dj-sticky-view dj-sticky-root` wrapper code path.

2. **What the tag does NOT do**:
   * Does not write to `_sticky_preserved` (the consumer owns its
     lifecycle).
   * Does not call the survivor's `mount()` again (sticky's whole
     point is to skip mount).
   * Does not write the subtree HTML — only the placeholder. The
     stashed DOM (held by the client in `stickyStash`) becomes the
     source of truth on reattach.
   * Does not emit a `sticky_hold` frame — the consumer continues to
     own that emission.

3. **Wire format**: no change. The new path emits HTML the consumer
   slot scan and client reattach already understand.

4. **Slot placeholder body**: deliberately empty
   (`<div dj-sticky-slot="<id>"></div>`). The client's `replaceWith`
   throws away the placeholder DOM whole, so any "loading…" content
   inside would be unreachable.

### Why not the originally-framed Options A/B/C/D (cookie / header / WS-handshake)

The task framing proposed cookie / header / WS-handshake metadata /
hybrid as transport options. **None applies** because the bridge from
"client holds X" to "server-side template tag" exists already on the
WS `live_redirect_mount` path: `consumer._sticky_preserved` is the
freshest, most reliable answer. Re-implementing it via cookie or header
would solve a problem that is already solved, less reliably (cookie
staleness, cross-tab inconsistency, header missing on direct nav, CDN
`Vary` complications).

The cookie/header/handshake mechanisms only become relevant for an
**orthogonal future feature**: preserving sticky state across HTTP-GET
hard reloads / Service Worker resumes. That is explicitly out of
scope for this ADR.

### Why not push the auto-detect into the consumer's slot scan instead

Alternative: have the consumer's existing slot scan also accept
`[dj-sticky-view="<id>"]` as a match, detect collision with the freshly-
mounted child, and choose the survivor over the fresh child.

Rejected:

* Doubles the work — the fresh child has already been instantiated,
  `mount()` has run, child template has been rendered, HTML has been
  stamped. Throwing it away after the fact wastes CPU, fires
  `mount()` side effects (DB queries, presence join, async tasks)
  that are then orphaned.
* Side effects in `mount()` (e.g. `start_async`, `track_presence`,
  `listen` to a channel) cancelled after-the-fact are at best wasteful,
  at worst leak.

The tag-time check is **early enough** that the fresh mount never happens.

### Why a `_sticky_auto_reattached` set tracker

After the tag re-registers the survivor, the consumer's post-render
slot scan would otherwise iterate `_sticky_preserved` and try to
`_register_child` the same survivor again — hitting `ValueError`.

The set-tracker design (rather than popping from `_sticky_preserved`)
keeps `_register_child`'s collision guard intact for genuine
duplicate-id template errors, while giving the slot-scan code a single
narrow check ("skip IDs the tag already claimed").

The set is reset at the top of `handle_mount` and
`handle_live_redirect_mount` to prevent leakage across navigations.

## Wire protocol

No new frames. No changes to `sticky_hold`, `mount`, or
`live_redirect_mount` payloads. The change is server-internal: which
HTML the tag emits, which path the slot scan takes.

## Backwards compatibility

* **Old client + new server**: clients shipped with sticky support
  (v0.6.0+) already understand `[dj-sticky-slot]` from ADR-011. A
  pre-sticky client would not have stashed any subtree to reattach,
  so `_sticky_preserved` would be empty and the tag falls through to
  fresh-mount. Equivalent to current behavior.
* **New client + old server**: client semantics unchanged. Server
  emits the same shape it always did (fresh-mount on Dashboard return),
  client's existing reattach-on-slot path runs the same way. No
  regression.

## Failure modes

| Mode | Behavior |
|------|----------|
| `_ws_consumer` is `None` (HTTP GET / direct address-bar) | Fall through to fresh-mount. Equivalent to today. |
| `_sticky_preserved` is empty | Fall through to fresh-mount. |
| `sticky_id` mismatch between survivor and tag invocation | Cannot happen by construction — `sticky_id` is a class attribute. |
| Two `{% live_render ... sticky=True %}` for same class in same render | `_STICKY_IDS_SEEN_KEY` raises `TemplateSyntaxError` (existing check, runs before auto-detect). |
| Auto-reattach claimed; consumer slot scan finds slot for same id | Slot scan checks `_sticky_auto_reattached` and skips. Survivor already on new parent. Identical to existing flow. |

## Test contract

Implementations of this ADR MUST cover:

1. **No consumer**: render context with no `_ws_consumer` produces
   output containing `dj-sticky-view`, NOT `dj-sticky-slot`.
2. **Consumer with empty `_sticky_preserved`**: fresh-mount path runs.
3. **Consumer with preserved sticky for our id**: tag emits the slot
   placeholder, registers survivor on new parent, adds id to
   `_sticky_auto_reattached`, does NOT call `child_cls()`.
4. **Consumer holds a different id**: tag falls through to fresh-mount;
   `_sticky_auto_reattached` does not gain entry.
5. **Dashboard→Settings→Dashboard end-to-end**: through `_FakeConsumer`,
   asserts instance identity across A→B→A and that state set on round 1
   survives round 3.
6. **Dashboard→Reports (no slot) → Dashboard**: audio sticky dropped
   after Dashboard→Reports; returning to Dashboard fresh-mounts.
   Asserts `dashboard_b.audio is not dashboard_a.audio`.
7. **`sticky_hold` frame includes auto-reattached IDs** so the client
   doesn't drop them from `stickyStash`.
8. **`mount` frame body shape**: contains `dj-sticky-slot="<id>"`,
   does NOT contain `dj-sticky-view="<id>"` for the auto-reattached id.

## Out of scope (explicit non-goals)

* HTTP GET / hard-reload preservation (Service Worker / cookie territory).
* Cross-tab disambiguation.
* Multi-worker / Redis-backed sticky map.
* Cross-document View Transitions for sticky reattach animation
  (composes cleanly with ADR-013 but separate work).
* System check for "`{% live_render sticky=True %}` AND a hand-coded
  `<div dj-sticky-slot>` for the same id in the same template" —
  deferred to a follow-up.

## Open questions

1. Should the tag log a debug message when it auto-reattaches?
   **Recommendation**: yes — `logger.debug("auto-reattach %s on %s",
   sticky_id, view_path)`. Parity with existing sticky workstream
   logging.

2. Should the placeholder include `data-djust-embedded` for parent-
   routed events emitted from JS BEFORE the `replaceWith` lands?
   **Recommendation**: no — the placeholder is replaced wholesale on
   `mount` frame application before any new user interaction can reach
   it; the inserted-from-stash subtree already carries the correct
   attribute.
