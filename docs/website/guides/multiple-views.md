---
title: "Several LiveViews on One Page"
slug: multiple-views
section: guides
order: 6.9
level: intermediate
description: "An eager page view, dj-lazy views and a mount_batch on one WebSocket or SSE stream: each is live on its own, and its events, pushes and presence go to it."
---

# Several LiveViews on One Page

A page can hold more than one LiveView:

- the **page view**, the one the page URL renders (`dj-view` on the `dj-root`);
- **lazy views**, `<div dj-view="myapp.views.Widget" dj-lazy>` containers that mount when they scroll into view, on the first click or hover, or when the browser is idle;
- the views of a **`mount_batch`**: lazy views that hydrate together are mounted by one frame.

They share one connection, a WebSocket or (when WebSocket is off or blocked) an SSE stream; [what each transport carries](#transports) is below. Each is a LiveView of its own on it: it has its own state, its own render, its own tick, presence and group memberships, and it is torn down on its own. A click in one view runs on that view, a server push goes to the views of the class it was sent to, and mounting a lazy view does not touch the page view.

```html
<div dj-root dj-view="myapp.views.Dashboard">
  <button dj-click="refresh">Refresh</button>
</div>

<!-- Mounts when it scrolls into view. Alongside the dashboard, not instead of it. -->
<div id="stats" dj-view="myapp.views.StatsWidget" dj-lazy></div>

<!-- Mounts on the first click. -->
<div id="chat" dj-view="myapp.views.ChatWidget" dj-lazy="click"></div>
```

Put a lazy container **beside** the page view's `dj-root`, as in the example, not inside it. A page mount morphs the root's content and strips the `data-djust-target` the client set on a container inside it, so the lazy view's mount reply finds no target (the client logs `mount: target not found` and unmounts it).

## What each view gets

| | |
|---|---|
| **Events** | A `dj-*` event from inside a lazy view runs on that view. An event from the page view runs on the page view. |
| **Server push** | `push_to_view("myapp.views.StatsWidget", ...)` runs the handler on every view of that class on the socket, and only those. Two views of one class both take it. |
| **Presence and `db_notify`** | Each view joins and leaves its own presence group and `listen()` channels, and handles its own events. |
| **State** | Each view keeps its own state and its own VDOM. Two lazy views of one class do not share a rendered tree. |
| **Authorization** | Every view runs its own login, permission and object-permission checks when it mounts, and an [explicit-exposure](../state/explicit-exposure.md) view authorizes each of its events against its own session binding. A lazy view the user may not open is refused on its own: its container shows the refusal, the page view and the other views stay live, the socket stays open and the page does not navigate ([A refused view](#a-refused-view)). A refusal at event time ends that view only. |
| **Tick, async work** | `tick_interval` / `handle_tick` and `start_async` work run per view, and their updates go to that view's container. |
| **Uploads** | A file input (`dj-upload`, `dj-upload-drop`, `dj-paste`) inside a lazy view registers its upload with that view, so the view's `allow_upload` slots apply. The binary chunks carry the upload's `ref` and reach the view that registered it. |
| **Hooks** | `this.pushEvent()` in a `dj-hook` inside a lazy view runs the event on that view. |
| **Teardown** | Navigating (`live_redirect`), a new page mount or a disconnect tears every view down. Removing a container from the page tears down just its view: the client sends [`unmount`](#unmounting-a-view) for it once it has stayed out of the document for about a second (a container a morph or a view transition puts back in time keeps its view). Each container gets its own grace period: one removed while another's is running is unmounted about a second after it left, even if nothing else on the page changes in between. |

A view replaced by a new mount of the same container (hydrating `#stats` again) is torn down on its own.

## How a view is addressed

Every view on the socket has an address, the `target_id` of its container. The stock client picks the container's `id` (or generates one) and stamps it as `data-djust-target`:

```html
<div id="stats" dj-view="myapp.views.StatsWidget" dj-lazy data-djust-target="stats">...</div>
```

- The page view has no address. A frame that names none is for the page view, so a page with no lazy views behaves exactly as before and its frames are unchanged.
- A `mount` frame with a `target_id` adds a view beside the others. A `mount` without one is the page view's own mount and replaces every view on the socket, as `live_redirect` does.
- Each `mount_batch` entry names its container with `target_id`.
- The client puts `target_id` on every frame it sends for an element inside a lazy view's container: `event` frames (clicks, `dj-input`, `dj-model`, `dj-poll`, forms, `dj-hook` `pushEvent`, `djust.js` commands run from an element), `request_html`, `upload_register` and `upload_resume`. The server puts it on every frame a lazy view sends (`patch`, `html_update`, `html_recovery`, `noop`, `error`, `embedded_update`, ...). Binary upload chunk, complete and cancel frames carry only the upload's `ref`; the server resolves it to the view that registered it. Page-level frames (`navigate`, `flash`, `page_metadata`, `push_event`, ...) are not stamped.
- Each view numbers its own VDOM frames: the client keeps one cursor per container, so a gap in one view asks for that view's recovery HTML and not the page's.
- A frame that names a view that is not mounted (torn down, forged, never mounted) is refused. It is never answered by another view.

An older client that predates `target_id` sends events with none: they go to the page view, or, on a socket that only hosts batched views, to the view mounted last, as before.

### A refused view

A view beside the page view that the user may not see (a login is required, a permission or the object permission is missing, an `on_mount` hook redirects) is refused on its own. The server answers with a `view_refused` frame addressed to the view's container (over `mount_batch`, an entry of the reply's `refused[]` array), never with the page-level `navigate` a page view's refusal is:

```json
{"type": "view_refused", "target_id": "members", "reason": "login_required", "code": "permission_denied", "to": "/accounts/login/?next=/dashboard/"}
```

`reason` is `login_required`, `permission_denied` or `redirect`; `to` is the sign-in (or redirect) URL when there is one. The client then

- replaces the container's content with `<div class="dj-view-refused" role="alert">` holding a short message and, for `login_required` and `redirect`, a link to `to`; the container gets `data-djust-refused="<reason>"` to style. A `to` that is neither a same-origin path nor an http(s) URL gets no link;
- leaves everything else alone: the page's URL, the page view and the other views are untouched, and nothing navigates;
- forgets the view, so a reconnect does not mount it again (a reload, after the user signs in, does);
- fires `djust:view-refused` on `window` with `detail: {targetId, view, reason, code, to, container}`, so an application can replace the default content or show its own sign-in prompt.

The refused view is torn down on its own, and its address answers nothing afterwards: an event for it is refused with `code: "view_unavailable"` (the error frame carries the event's `ref`). A refusal at event time ends that view the same way: when a view's re-check fails (`reauth_on_event`, a revoked session or permission), that view's container shows the refusal (`login_required`, with the sign-in URL), the socket stays open and no page navigates; only the page view's own revoked authority sends the browser to the login page and closes the socket (4403). The frame's `code` is always `permission_denied`; applications should key on `reason`. A `mount_batch` entry that names no usable `target_id` appears in `refused[]` with an empty `target_id`: there is no container to show it in, so the client shows nothing for it (and, unlike the old `navigate[]` entry, it does not navigate).

### Unmounting a view

```javascript
window.djust.liveViewInstance.unmountView("stats");
```

sends `{"type": "unmount", "target_id": "stats"}`. The server releases that view (its groups, its tick, its waiters, its presence, its uploads) and leaves the others live. Unmounting a view that is not mounted does nothing. The client calls it for you when a view's container is removed from the document; call it yourself to release a view whose container stays. An event from inside a container whose view was unmounted keeps the container's address and is refused; it never runs on the page view.

Two views that share a channel-layer group (two views of one class, the same `listen()` channel, the same presence key) each keep it until the last of them goes: unmounting one does not silence the other.

### Limits

`LIVEVIEW_CONFIG["max_views_per_connection"]` (default `64`) bounds how many views one socket hosts besides the page view. A mount past the limit is refused. The address is client-supplied, so it is checked: 1 to 200 printable ASCII characters, with no whitespace and none of `" ' ` \ < >`. An address that does not pass is refused, never taken for a page mount. The limit is per connection, so what one browser can hold is the limit times its connections: over SSE, `DJUST_SSE_MAX_SESSIONS_PER_CLIENT` sessions (default 20) of up to 64 views each. Mounts and unmounts count against the connection's message rate limit like every other frame; a burst past it is answered `rate_limited` and a sustained flood closes the connection.

## Transports

The contract above holds on every connection the page can have; what differs is what the connection carries.

| | WebSocket | SSE stream | Page POST (no `EventSource`) |
|---|---|---|---|
| Page view | yes | yes | yes |
| Lazy views (`dj-lazy`) | one `mount` frame each, or one `mount_batch` | one `mount` POST each (no `mount_batch`) | not hydrated: `djust:error` with `code: "view_unavailable"` |
| Events, routed by view | yes | yes | the page view's and `{% live_render %}` children's; a lazy view's are refused |
| Hooks (`pushEvent`) | to the view they sit in | to the view they sit in | no |
| Uploads | yes | no | no |
| Server push, presence, `db_notify`, tick | per view | none, for any view (SSE has no server push) | none |
| `start_async` and background work | per view | per view | no |
| Saved state, snapshots, authorization | per view | per view | the page view's |
| Unmount one view | `unmount` frame | `unmount` frame | n/a |
| Teardown | a disconnect, `live_redirect` or a page mount | the stream closing, `live_redirect` or an `unmount` frame | n/a |
| Reconnect | every view mounts again on the new socket | the page view mounts, then every view beside it | n/a |
| `max_views_per_connection` | yes | yes | n/a |

Over SSE the session is the connection: a lazy container hydrates once the page view is mounted, with a `mount` POST that names its container (`target_id`), and the server addresses every frame of that view with the same `target_id`. A page with no lazy views is unchanged. A page that has no page view to mount a session for (only `dj-lazy` containers) cannot go live over SSE; its views are reported `view_unavailable`.

Where a view cannot go live, the client says so (`djust:error` with `code: "view_unavailable"`, which the dev error overlay shows) instead of leaving a container that looks interactive, and an event from inside such a container is refused. It is never sent to the page view, which would run it there.

- **Embedded `{% live_render %}` children are routed on every transport.** Over HTTP-only the request renders the page once to register its children, then validates, authorizes and runs the handler on the child the event's `view_id` names, and answers with the child's own HTML. See [HTTP-Only Mode](http-only-mode.md#embedded-views-over-the-http-fallback).
- **State is per view on every transport.** An [explicit-exposure](../state/explicit-exposure.md) view's `persist="server"` state is stored under its own address, so two views of one class on one page (and the page view) each restore their own after a reconnect. A view beside the page view takes part in no navigation snapshot: the signed snapshot the client keeps for Back is the page view's, and a sibling's frames neither replace nor revoke it.

## Behaviors worth knowing

- **Reconnect.** After the socket reconnects (or, over SSE, the stream does) the client mounts the page view and every lazy view again; each starts a new VDOM cursor with its mount reply.
- **Same class twice.** Two lazy views of one class on one page are two views: each has its own state and its own cached Rust view.
- **Events with no element.** An event sent from code (`djust.handleEvent("name")`) has no container to belong to and goes to the page view. Events that come from a `dj-*` attribute in a lazy view carry its address. `dj-patch` and `dj-navigate` act on the page's URL and are the page's, wherever they sit.
- **Latency is shared.** The connection's receive loop (or, over SSE, its dispatch lock) and render lock belong to the connection, not to a view: an event handler that takes a second delays the events of every other view on the socket by that second. Routing is independent; latency is not. Move slow work into `start_async`, which runs off the lock.
- **Upload slot names are page-wide on the client.** The client keeps one upload configuration per slot name, so two views that each declare an `allow_upload("doc")` with different limits are validated client-side against the last one mounted. The server applies each view's own limits.
- **Group-less sends are dropped when several views are mounted.** A raw `channel_layer.group_send` of a `server_push`, `presence_event` or `db_notify` message that names no `group` cannot be told apart per view, so a socket hosting more than one view drops it. `push_to_view`, `PresenceManager` and the `db_notify` listener already add it. With one view mounted nothing changes.
- **Back and Forward.** A view beside the page view is not part of the page's saved state: the signed snapshot the client keeps for Back and Forward is the page view's, so lazy views mount fresh (empty, then hydrated) when the user comes back, while the page view restores as before. This holds on every transport.
- **Without `EventSource`.** A browser with neither a WebSocket nor `EventSource` cannot host a lazy view at all: hydration is refused loudly (`djust:error`, `code: "view_unavailable"`) instead of leaving a dead container, and the page view keeps working through the HTTP fallback. There is nothing for that fallback to refuse per view, because it hosts none.
- **A page of lazy views only, over SSE.** An SSE session is mounted by its page view (the stream's `?view=`), so a page with only `dj-lazy` containers has no session to host them. Give it a minimal page view, for example a `dj-root` view with a `<noscript>`-sized template, and the lazy views mount beside it. (Over WebSocket a page of lazy views only works as before.)
- **After `live_redirect`.** `live_redirect` replaces the views on the socket and swaps the page's content without initializing the page again, so the destination's own `dj-lazy` containers do not hydrate until a full or TurboNav load.
- **Time travel, the debug panel and bug-capture replay** work on the page view (or, on a batch-only socket, the last view); they are developer tools, not part of the live contract.
