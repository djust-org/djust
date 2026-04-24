# ADR-011: Sticky LiveViews

**Status**: Accepted
**Date**: 2026-04-23
**Deciders**: Project maintainers
**Related**: [ADR-007](007-package-taxonomy-and-consolidation.md),
[`python/djust/mixins/sticky.py`](../../python/djust/mixins/sticky.py),
[`python/djust/templatetags/live_tags.py`](../../python/djust/templatetags/live_tags.py),
[`python/djust/static/djust/src/45-child-view.js`](../../python/djust/static/djust/src/45-child-view.js),
PRs #966 (Phase A), #967 (Phase B), #969 (Phase C)

---

## Summary

djust LiveViews unmount and re-mount on every ``live_redirect`` — the
audio player dies when you navigate, the sidebar restarts, the
notification center loses its queue. This ADR defines **Sticky
LiveViews**: mark a child LiveView with ``sticky = True``, embed it via
``{% live_render "path.to.View" sticky=True %}``, and declare a
``<div dj-sticky-slot="<id>">`` in the destination layout. The server
keeps the Python instance alive, the client detaches the DOM subtree
into an in-memory stash, and both reconnect at the new slot with
state, form values, scroll, focus, and background tasks intact.

## Context

### What today's `live_redirect` does

When a LiveView calls ``self.live_redirect("/settings/")``, the client
sends ``live_redirect_mount`` and the consumer runs a clean cycle:
tear down the current view's channel groups, tasks, uploads, child
views, and VDOM state — then re-mount the destination. This is correct
for the primary page but catastrophic for persistent widgets:

* An audio player that re-mounts on every nav loses the `<audio>`
  element's playback position (a new element ≠ the old one — the
  browser stops and restarts the stream).
* A sidebar that re-mounts flashes its skeleton state twice per click.
* A notification center's in-memory queue vanishes every nav.
* A wizard's preview pane resets its form values when the user
  navigates to a neighboring step.

### What Phoenix does

Phoenix LiveView 1.0 added ``live_render(@socket, ChildLV, sticky:
true)`` with the same semantics: the sticky LV's process is preserved
across ``live_redirect`` if the destination layout re-declares it at a
matching mount point. Phoenix's implementation keys on the sticky LV's
process id + session token; djust keys on ``sticky_id`` (stable
identity chosen by the developer or defaulted from the class name).

### App-shell pattern

Modern web apps layer persistent widgets on top of page content:

```
+--------------------------------+
|  Sidebar | [ page content ]   |
|  + audio player at the bottom |
|  + notification bell top-right|
+--------------------------------+
```

Navigation swaps the page content; the sidebar, audio player, and
notification bell must not flicker. Without sticky LiveViews, each
nav costs the same as a full-page reload in user-perceived
jank, negating the point of `live_redirect`.

## Decision

Ship a three-surface API:

1. **Class attribute** — ``LiveView`` gains ``sticky: bool = False``
   and ``sticky_id: Optional[str] = None``. Both are opt-in: only
   classes that explicitly declare ``sticky = True`` are eligible,
   and the ``sticky_id`` is the stable identity shared between server
   and client. Default `None` falls back to ``class.__name__.lower()``
   when the tag doesn't pin one.
2. **Template tag** — ``{% live_render "dotted.path" sticky=True %}``
   is the existing Phase A embedding tag with a new ``sticky`` kwarg.
   The tag validates that the resolved class has ``sticky = True``
   (raises ``TemplateSyntaxError`` otherwise) and renders the outer
   wrapper with ``dj-sticky-view="<id>"`` + ``dj-sticky-root``
   attributes.
3. **Slot markers** — destination layouts declare
   ``<div dj-sticky-slot="audio-player"></div>`` at the re-attachment
   point. The client's post-mount reattach walks these and swaps each
   slot with the stashed subtree via ``replaceWith()``.

### Why not class-attribute-only?

A class attr alone (e.g. ``sticky = True`` on the class, no tag
kwarg) can't decide *where* the sticky surfaces in the destination
layout. Two pages might want the same sticky audio player at
different mount points; the tag kwarg + slot marker split lets each
layout declare its own re-attachment point.

### Why not tag-only?

A tag-only opt-in (``sticky=True`` without a class attr) would make
accidental preservation possible — any embedded child would become
sticky if the template author passed the kwarg. Requiring
``Cls.sticky = True`` means the class author has explicitly opted
into the preservation contract (background tasks stay running,
instance attrs survive navigation, auth is re-checked every nav).

### Why `dj-sticky-slot` markers?

Slot markers are decoupled from the sticky view's identity — the
destination page knows nothing about the class, only the ``sticky_id``
string. This makes it trivial for a layout to declare "I accept a
sticky-audio-player at this point" without importing the class or
knowing where it lives in the source tree.

## Wire protocol

Two new frames join the existing ``child_update`` (Phase A):

### `sticky_hold` (server → client, new)

Sent BEFORE the ``mount`` frame on ``handle_live_redirect_mount``.
Enumerates the set of sticky ids that survived the server-side
staging step (both "the child class opted in" and "auth re-check
passed for the new request"). The client reconciles its stash
against this list — any stash entry NOT in ``views`` is dropped and
a ``djust:sticky-unmounted`` event fires with
``reason='server-unmount'``.

```json
{
  "type": "sticky_hold",
  "views": ["audio-player", "notification-center"]
}
```

**Ordering contract**: ``sticky_hold`` MUST arrive BEFORE ``mount``.
The client's mount handler eagerly calls ``reattachStickyAfterMount``,
which walks the stash and replaces ``[dj-sticky-slot]`` elements. If
``sticky_hold`` arrived after ``mount``, auth-revoked stickies would
be reattached before reconciliation ran. The consumer enforces this
in :meth:`handle_mount`: when called with ``sticky_preserved``
(from the ``handle_live_redirect_mount`` flow), the frame is emitted
immediately before the ``mount`` ``send_json``.

### `sticky_update` (server → client, new)

Sibling of ``child_update`` from Phase A. Same shape; different
routing. The client's ``45-child-view.js`` scopes patches to the
sticky subtree (``[dj-sticky-view="<id>"]``) via a new
``applyPatches(patches, rootEl)`` variant in ``12-vdom-patch.js`` —
when ``rootEl`` is non-null, node lookups / focus save-restore /
autofocus queries all scope to that subtree.

```json
{
  "type": "sticky_update",
  "view_id": "audio-player",
  "patches": [ {"type": "SetText", "path": [0, 1], "d": "..."}, ... ],
  "version": 7
}
```

Per-child VDOM versions are tracked on the client via
``clientVdomVersions: Map<view_id, number>`` with ``"__root"`` as the
sentinel for top-level patches.

### `child_update` (server → client, existing Phase A frame)

Same shape; now fully wired in Phase B via the scoped applier.
Carries non-sticky embedded-child patches.

## DOM attributes

| Attribute | Where | Purpose |
|---|---|---|
| `dj-sticky-view="<id>"` | Outer wrapper emitted by `{% live_render %}` when `sticky=True` | Marks the subtree to detach into stash before nav; also the scoping root for `sticky_update` patches. |
| `dj-sticky-root` | Same wrapper | Marks the wrapper as a "not-a-page-root" to every client module that walks `[dj-view]` looking for the top-level view (`03-websocket.js`, `40-dj-layout.js`, `24-page-loading.js`, `12-vdom-patch.js` autofocus). Without this, a sticky child would masquerade as the page's root view and get its innerHTML replaced on mount. |
| `dj-sticky-slot="<id>"` | Destination layout, author-declared | Re-attachment point. The client's `reattachStickyAfterMount` replaces the slot element with the stashed subtree via `replaceWith()`. |
| `data-djust-embedded="<view_id>"` | Outer wrapper (both sticky and non-sticky embeds, from Phase A) | Routes inbound events to the correct child; also how `getEmbeddedViewId` surfaces the id on outbound events. |

## Client-side flow

1. **Outbound navigation** (user clicks a link, `live_redirect` fires):
   `18-navigation.js` calls ``djust.stickyPreserve.stashStickySubtrees()``
   BEFORE sending ``live_redirect_mount``. This walks every
   ``[dj-sticky-view]`` in the DOM, detaches the subtree via
   ``parentNode.removeChild``, and stores it in ``stickyStash``
   keyed by ``dj-sticky-view`` id. DOM identity is preserved —
   form values, focus, scroll, and third-party widget references
   (e.g. ``<video>`` players) all survive.

2. **`sticky_hold` frame arrives**: ``reconcileStickyHold(views)``
   drops any stash entry NOT in the authoritative list. Each dropped
   subtree receives a ``djust:sticky-unmounted`` CustomEvent with
   ``reason='server-unmount'``.

3. **`mount` frame arrives**: the mount handler replaces the page
   content with the new HTML, then calls ``reattachStickyAfterMount()``.
   This walks every ``[dj-sticky-slot]`` in the fresh DOM, looks up
   the corresponding stash entry, and replaces the slot with the
   stashed subtree via ``Element.replaceWith``. Any stash entry with
   NO matching slot in the new layout fires
   ``djust:sticky-unmounted`` with ``reason='no-slot'``.

4. **Post-reattach lifecycle**: the surviving ``[dj-sticky-view]``
   element dispatches ``djust:sticky-preserved`` with ``{sticky_id}``.
   Subsequent ``sticky_update`` frames apply scoped VDOM patches to
   this subtree without touching the parent view.

5. **Abnormal WS close**: ``03-websocket.js`` onclose handler calls
   ``djust.stickyPreserve.clearStash()`` on the abnormal-disconnect
   path (after the ``_intentionalDisconnect`` early return). The
   server will re-mount any sticky views from scratch on reconnect
   — keeping detached subtrees from a dead session would only cause
   ``no-slot`` unmount events on the next navigation.

## Server-side flow

1. **`handle_live_redirect_mount` invoked**: the consumer calls
   ``old_view._preserve_sticky_children(new_request)``. This walks
   the parent's ``_child_views``, filters to children with
   ``sticky is True``, and per-sticky re-runs
   ``check_view_auth_lightweight(child, new_request)`` — if auth
   fails for the destination URL, the child is discarded (with
   ``_on_sticky_unmount()`` called on it to cancel background tasks).
   Survivors are returned as ``{sticky_id: child}`` and stashed on
   ``self._sticky_preserved`` on the consumer.

2. **Old view torn down**: channel groups left, upload manager
   cleaned, tick task cancelled. Sticky children are popped from
   ``old_view._child_views`` WITHOUT invoking
   ``_cleanup_on_unregister`` (which would cancel their tasks).

3. **New view mounted**: ``handle_mount(data, sticky_preserved=...)``
   runs. Inside mount, after the new parent's template renders, the
   rendered HTML is scanned for ``[dj-sticky-slot]`` attributes. The
   intersection of ``sticky_preserved.keys()`` and the scanned set
   is the final survivor set; the non-intersection has
   ``_on_sticky_unmount()`` called and is dropped. Final survivors
   are re-registered on the new parent via ``_register_child``.

4. **`sticky_hold` frame emitted** BEFORE the ``mount`` frame, with
   the final survivor set's keys.

5. **`mount` frame emitted** with the new HTML.

6. **Subsequent events**: normal Phase A dispatch. Events targeted
   at a sticky child route via ``view_id`` through the parent's
   registry; the child's re-render fans out as a ``sticky_update``
   frame (scoped patches) rather than a root-level ``patch``.

## Security model

1. **Per-sticky auth re-check** — ``_preserve_sticky_children`` calls
   ``djust.auth.core.check_view_auth_lightweight(child, new_request)``
   for every sticky before staging it. A view whose
   ``permission_required`` / ``check_permissions`` would deny the
   new request is unmounted via ``_on_sticky_unmount`` and NOT
   carried forward. This closes the primary threat: a user who
   navigates from a page they can access to one they can't must
   not retain a sticky view tied to the forbidden context.
   MITIGATED.

2. **XSS via `sticky_id`** — the attribute value is rendered through
   ``django.utils.html.escape`` and wrapped via ``build_tag``-style
   string concatenation in ``live_tags.py``. An attacker-controlled
   sticky_id (not currently user-reachable but defended-in-depth)
   cannot break out of the attribute context; ``CSS.escape`` on
   the client-side selector lookup prevents selector injection
   even if a future bug allowed a malicious id through. SAFE.

3. **Client stash DoS** — the stash is bounded by the developer-
   authored template content. An attacker cannot inflate the stash
   because the set of ``[dj-sticky-view]`` elements is fixed by
   the server-rendered HTML. Entries are keyed by ``sticky_id`` so
   duplicates coalesce (``stashStickySubtrees`` is idempotent). The
   stash is cleared on abnormal WS close and on `reload`. BOUNDED.

4. **Inbound frame forgery** — a malicious client sending
   ``sticky_update`` or ``sticky_hold`` at the consumer is rejected:
   the consumer's ``receive`` dispatch table lists only the inbound
   types the server accepts (``event``, ``mount``, ``ping``,
   ``url_change``, ``live_redirect_mount``, ``upload_*``,
   ``presence_heartbeat``, ``cursor_move``). ``sticky_update`` and
   ``sticky_hold`` are server-to-client ONLY and fall through the
   allowlist with a "Unknown message type" log. REJECTED.

5. **CSS selector injection** — client-side selectors that interpolate
   ``sticky_id`` use ``CSS.escape`` to defeat attempts like
   ``audio-player"],script:foo`` from forming a valid composite
   selector. SAFE.

6. **Allowlist still applies** — the Phase A
   ``DJUST_LIVE_RENDER_ALLOWED_MODULES`` prefix-allowlist still
   gates which dotted paths ``{% live_render %}`` may resolve,
   regardless of the ``sticky=True`` kwarg.

7. **Tenant boundary** — currently sticky views persist across any
   ``live_redirect`` within the same WS session. If a deploy puts
   two tenants on one WS (unusual — we strongly recommend per-tenant
   WS scoping), a sticky child carrying tenant-A context would
   survive a nav to tenant-B until the next ``check_view_auth_lightweight``
   failure. Explicit tenant-tagged staging is tracked as a future
   refinement; for now the recommendation is to isolate WS by tenant
   at the routing layer.

## Threat model summary

| Threat | Status |
|---|---|
| Auth bypass via retained sticky across permission revocation | MITIGATED (per-sticky auth re-check) |
| XSS via sticky_id attribute value | SAFE (escape on server, CSS.escape on client) |
| Client stash DoS | BOUNDED (content-limited, idempotent, cleared on abnormal close) |
| Inbound frame forgery (`sticky_update` / `sticky_hold`) | REJECTED (consumer allowlist) |
| CSS selector injection | SAFE (CSS.escape) |
| Cross-tenant sticky bleed on shared WS | FUTURE WORK (recommend per-tenant WS) |

## Failure modes

### No matching slot in destination layout

If the destination template does not declare
``<div dj-sticky-slot="<id>"></div>`` for a surviving sticky, the
client's ``reattachStickyAfterMount`` fires
``djust:sticky-unmounted`` with ``reason='no-slot'`` on the stashed
subtree and drops it from the stash. The server has already
filtered this on its side (via the post-render slot scan in
``handle_mount`` — see :meth:`handle_mount` call into
``_find_sticky_slot_ids``), so the no-slot path is a defense-in-depth
against template drift between server-rendered HTML and whatever
the client's post-mount DOM ended up looking like (e.g. hooks or
third-party code removed the slot).

### Sticky id collision across two `{% live_render %}`

If two ``{% live_render "A" sticky=True %}`` tags in the same
template resolve to the same ``sticky_id`` (typically: same class,
no explicit ``sticky_id`` kwarg), the tag raises
``TemplateSyntaxError`` at render time. Collisions at render time
are loud and unambiguous — no silent fallback that swaps state
between two "audio-player"s.

### `handle_mount` failure mid-redirect

If the new view's ``mount()``, ``get_context_data()``, or
``render_to_string`` raises, the consumer's
``handle_live_redirect_mount`` catches the exception in a
``try/finally`` and drains ``self._sticky_preserved`` — every
staged sticky receives ``_on_sticky_unmount()`` and the dict is
cleared. Without this, a render failure on the NEW view would
leave preserved sticky instances alive on the consumer with
background tasks still running against a "zombie" parent.

### Abnormal WebSocket close

The client's ``03-websocket.js`` onclose handler, on the
abnormal-close branch (after the ``_intentionalDisconnect`` early
return), calls ``djust.stickyPreserve.clearStash()``. This prevents
orphaned subtrees from a dead session bleeding into a new session
on reconnect. The server's reconnect flow re-mounts all sticky
views from scratch.

### Resolver404 on destination URL

If the new URL fails to resolve (no matching URL pattern),
``_build_live_redirect_request`` returns ``None`` and
``handle_live_redirect_mount`` treats all staged stickies as
discarded — each gets ``_on_sticky_unmount`` called and the stash
is drained before the consumer returns. This closes the edge case
where a sticky's ``check_permissions`` relies on
``request.resolver_match.kwargs`` — without a resolved match, that
attribute access would either ``AttributeError`` or silently pass
using stale data from the old request.

### Disconnect mid-redirect

Narrow window: after ``_preserve_sticky_children`` has staged
survivors onto ``self._sticky_preserved`` but BEFORE
``handle_mount`` has reattached them. Phase C Fix F2 drains the
dict in ``LiveViewConsumer.disconnect`` — each staged sticky
receives ``_on_sticky_unmount()`` so background tasks don't leak.

## Relationship to v0.7.0 `{% dj_activity %}`

React 19.2's ``<Activity>`` is **within-page show/hide** with
preserved state and background pre-rendering. Sticky LiveViews are
**across-page preservation** during ``live_redirect``. The two
features are complementary:

* Sticky = same LiveView instance survives navigation. Use for
  app-shell widgets: audio player, sidebar, notification center.
* Activity = same LiveView instance stays mounted but toggles
  visibility (via ``{% dj_activity "name" visible=... %}``). Use
  for tab panels, wizard steps, dashboards with pre-rendered
  neighbors.

``{% dj_activity %}`` ships in v0.7.0; see
``docs/website/guides/activity.md``.

### Future work tracked as out-of-scope for v0.6.0

* **Sticky across WS reconnect** — currently sticky views die on
  any WS close. Persisting their state to a sticky-state-store
  (same pattern as ADR-010 `UploadStateStore`) would let a sticky
  survive a network blip. Requires a state-serializer per sticky
  class — non-trivial. Deferred.
* **Cross-tab sticky via BroadcastChannel** — two tabs of the same
  app could share a single sticky audio player via BroadcastChannel.
  Deferred; design not blocked by v0.6.0.
* **`<head>` merging** — currently sticky views cannot inject
  stylesheets or meta tags into the destination layout's `<head>`.
  Put sticky-specific CSS in the initial layout. Tracked.

## Out of scope

* Sticky views that spontaneously re-parent (e.g. move from one
  slot to another mid-session). `sticky_id` is a static identity.
* Third-party WebRTC / MediaStream lifecycle — the DOM subtree
  survives, but if your app holds a MediaStream reference on the
  view instance it is your responsibility to detach/reattach the
  browser-side track on ``djust:sticky-preserved``.
* Sticky state replication across backend replicas — single-process
  only; apps needing cross-replica shared state should use the
  existing pg_notify / channel-layer broadcast patterns.

## Testing

| Layer | Count | File |
|---|---|---|
| Python unit (A + B + C) | 32 | `tests/unit/test_live_render_tag.py` (11 Phase A) + `tests/unit/test_sticky_preserve.py` (21 Phase B + C) |
| JSDOM | 20 | `tests/js/child_view.test.js` (7 Phase A) + `tests/js/sticky_preserve.test.js` (13 Phase B + 2 Phase C regression) |
| Integration | 6 | `tests/integration/test_sticky_redirect_flow.py` (3 Phase B + demo-app smoke tests) |

Coverage areas:

* Tag resolution + allowlist + HTML escaping (Phase A).
* `sticky=True` kwarg requires `Cls.sticky = True` — TemplateSyntaxError otherwise.
* Sticky state, background tasks, DOM identity survive `live_redirect`.
* Auth re-check revokes on permission change mid-session.
* `no-slot` + `server-unmount` unmount reasons fire correct CustomEvents.
* `sticky_hold` arrives BEFORE `mount` — ordering regression test.
* Rapid A→B→A navigation preserves instance identity.
* F1 regression: `skipMountHtml` mount branch reattaches sticky subtrees.
* F2 regression: `disconnect()` drains `_sticky_preserved` so background tasks don't leak.

## Implementation summary

* `python/djust/live_view.py` — `sticky` + `sticky_id` class attrs; `_sticky_preserved` init.
* `python/djust/mixins/sticky.py` — `StickyChildRegistry` + `_preserve_sticky_children` + `_on_sticky_unmount`.
* `python/djust/templatetags/live_tags.py` — `live_render` tag `sticky` kwarg + sticky wrapper emission.
* `python/djust/websocket.py` — `handle_live_redirect_mount` staging flow + `sticky_hold` + `sticky_update` emission + `disconnect()` sticky drain.
* `python/djust/static/djust/src/45-child-view.js` — `stickyStash`, `stashStickySubtrees`, `reconcileStickyHold`, `reattachStickyAfterMount`, `handleStickyUpdate`, `clearStash`.
* `python/djust/static/djust/src/12-vdom-patch.js` — `applyPatches(patches, rootEl)` scoped variant.
* `python/djust/static/djust/src/03-websocket.js` — `sticky_hold` + `sticky_update` dispatch, `clearStash` on abnormal close, F1 reattach in `skipMountHtml` branch.
* `python/djust/static/djust/src/18-navigation.js` — `stashStickySubtrees` call on outbound navigation.
* `docs/adr/011-sticky-liveviews.md` — this ADR.
* `docs/website/guides/sticky-liveviews.md` — user guide.
* `examples/demo_project/sticky_demo/` — runnable app-shell demo.
