---
title: "Several LiveViews on One Page"
slug: multiple-views
section: guides
order: 6.9
level: intermediate
description: "An eager page view, dj-lazy views and a mount_batch on one WebSocket: each is live on its own, and its events, pushes and presence go to it."
---

# Several LiveViews on One Page

A page can hold more than one LiveView:

- the **page view**, the one the page URL renders (`dj-view` on the `dj-root`);
- **lazy views**, `<div dj-view="myapp.views.Widget" dj-lazy>` containers that mount when they scroll into view, on the first click or hover, or when the browser is idle;
- the views of a **`mount_batch`**: lazy views that hydrate together are mounted by one frame.

They share one WebSocket. Each is a LiveView of its own on it: it has its own state, its own render, its own tick, presence and group memberships, and it is torn down on its own. A click in one view runs on that view, a server push goes to the views of the class it was sent to, and mounting a lazy view does not touch the page view.

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
| **Authorization** | Every view runs its own login, permission and object-permission checks when it mounts, and an [explicit-exposure](../state/explicit-exposure.md) view authorizes each of its events against its own session binding. A lazy view the user may not open is refused on its own: the page view and the other views stay live, and the socket stays open. The refusal still carries the redirect a page view's would (a login-required lazy view sends the browser to the login page, as it always has), so the browser leaves the page unless the page prevents it. A refusal at event time ends that view only. |
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

### Unmounting a view

```javascript
window.djust.liveViewInstance.unmountView("stats");
```

sends `{"type": "unmount", "target_id": "stats"}`. The server releases that view (its groups, its tick, its waiters, its presence, its uploads) and leaves the others live. Unmounting a view that is not mounted does nothing. The client calls it for you when a view's container is removed from the document; call it yourself to release a view whose container stays. An event from inside a container whose view was unmounted keeps the container's address and is refused; it never runs on the page view.

Two views that share a channel-layer group (two views of one class, the same `listen()` channel, the same presence key) each keep it until the last of them goes: unmounting one does not silence the other.

### Limits

`LIVEVIEW_CONFIG["max_views_per_connection"]` (default `64`) bounds how many views one socket hosts besides the page view. A mount past the limit is refused. The address is client-supplied, so it is checked: 1 to 200 printable ASCII characters, with no whitespace and none of `" ' ` \ < >`. An address that does not pass is refused, never taken for a page mount.

## Over SSE and the HTTP fallback

- **Embedded `{% live_render %}` children are routed on every transport.** Over HTTP-only the request renders the page once to register its children, then validates, authorizes and runs the handler on the child the event's `view_id` names, and answers with the child's own HTML. See [HTTP-Only Mode](http-only-mode.md#embedded-views-over-the-http-fallback).
- **Lazy and batched views need the WebSocket.** An SSE session and a page POST each host one view. A lazy view does not hydrate over them, and an event from one that hydrated before the socket dropped is refused (`djust:error` with `code: "view_unavailable"`), never sent to the page view, which would run it there.

## Behaviors worth knowing

- **Reconnect.** After the socket reconnects the client mounts the page view and every lazy view again; each starts a new VDOM cursor with its mount reply.
- **Same class twice.** Two lazy views of one class on one page are two views: each has its own state and its own cached Rust view.
- **Events with no element.** An event sent from code (`djust.handleEvent("name")`) has no container to belong to and goes to the page view. Events that come from a `dj-*` attribute in a lazy view carry its address. `dj-patch` and `dj-navigate` act on the page's URL and are the page's, wherever they sit.
- **Latency is shared.** The socket's receive loop and render lock belong to the connection, not to a view: an event handler that takes a second delays the events of every other view on the socket by that second. Routing is independent; latency is not. Move slow work into `start_async`, which runs off the lock.
- **Upload slot names are page-wide on the client.** The client keeps one upload configuration per slot name, so two views that each declare an `allow_upload("doc")` with different limits are validated client-side against the last one mounted. The server applies each view's own limits.
- **Group-less sends are dropped when several views are mounted.** A raw `channel_layer.group_send` of a `server_push`, `presence_event` or `db_notify` message that names no `group` cannot be told apart per view, so a socket hosting more than one view drops it. `push_to_view`, `PresenceManager` and the `db_notify` listener already add it. With one view mounted nothing changes.
- **After `live_redirect`.** `live_redirect` replaces the views on the socket and swaps the page's content without initializing the page again, so the destination's own `dj-lazy` containers do not hydrate until a full or TurboNav load.
- **Time travel, the debug panel and bug-capture replay** work on the page view (or, on a batch-only socket, the last view); they are developer tools, not part of the live contract.
