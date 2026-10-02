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

## What each view gets

| | |
|---|---|
| **Events** | A `dj-*` event from inside a lazy view runs on that view. An event from the page view runs on the page view. |
| **Server push** | `push_to_view("myapp.views.StatsWidget", ...)` runs the handler on every view of that class on the socket, and only those. Two views of one class both take it. |
| **Presence and `db_notify`** | Each view joins and leaves its own presence group and `listen()` channels, and handles its own events. |
| **State** | Each view keeps its own state and its own VDOM. Two lazy views of one class do not share a rendered tree. |
| **Authorization** | Every view runs its own login, permission and object-permission checks when it mounts, and an [explicit-exposure](../state/explicit-exposure.md) view authorizes each of its events against its own session binding. A lazy view the user may not open is refused on its own: the page view and the other views stay live, and the socket stays open. A refusal at event time ends that view only. |
| **Tick, async work** | `tick_interval` / `handle_tick` and `start_async` work run per view, and their updates go to that view's container. |
| **Teardown** | Navigating (`live_redirect`), a new page mount or a disconnect tears every view down. Removing one container tears down just its view (see [`unmount`](#unmounting-a-view)). |

A view replaced by a new mount of the same container (hydrating `#stats` again) is torn down on its own.

## How a view is addressed

Every view on the socket has an address, the `target_id` of its container. The stock client picks the container's `id` (or generates one) and stamps it as `data-djust-target`:

```html
<div id="stats" dj-view="myapp.views.StatsWidget" dj-lazy data-djust-target="stats">...</div>
```

- The page view has no address. A frame that names none is for the page view, so a page with no lazy views behaves exactly as before and its frames are unchanged.
- A `mount` frame with a `target_id` adds a view beside the others. A `mount` without one is the page view's own mount and replaces every view on the socket, as `live_redirect` does.
- Each `mount_batch` entry names its container with `target_id`.
- The client puts `target_id` on the `event`, `request_html`, upload and presence frames it sends for a view, and the server puts it on every frame a lazy view sends (`patch`, `html_update`, `html_recovery`, `noop`, `error`, `embedded_update`, ...). Page-level frames (`navigate`, `flash`, `page_metadata`, `push_event`, ...) are not stamped.
- Each view numbers its own VDOM frames: the client keeps one cursor per container, so a gap in one view asks for that view's recovery HTML and not the page's.
- A frame that names a view that is not mounted (torn down, forged, never mounted) is refused. It is never answered by another view.

An older client that predates `target_id` sends events with none: they go to the page view, or, on a socket that only hosts batched views, to the view mounted last, as before.

### Unmounting a view

```javascript
window.djust.liveViewInstance.unmountView("stats");
```

sends `{"type": "unmount", "target_id": "stats"}`. The server releases that view (its groups, its tick, its waiters, its presence, its uploads) and leaves the others live. Unmounting a view that is not mounted does nothing.

### Limits

`LIVEVIEW_CONFIG["max_views_per_connection"]` (default `64`) bounds how many views one socket hosts besides the page view. A mount past the limit is refused. The address is client-supplied, so it is checked: 1 to 200 printable ASCII characters, with no whitespace and none of `" ' ` \ < >`. An address that does not pass is refused, never taken for a page mount.

## Over SSE and the HTTP fallback

- **Embedded `{% live_render %}` children are routed on every transport.** Over HTTP-only the request renders the page once to register its children, then validates, authorizes and runs the handler on the child the event's `view_id` names, and answers with the child's own HTML. See [HTTP-Only Mode](http-only-mode.md#embedded-views-over-the-http-fallback).
- **Lazy and batched views need the WebSocket.** An SSE session and a page POST each host one view. A lazy view does not hydrate over them, and an event from one that hydrated before the socket dropped is refused (`djust:error` with `code: "view_unavailable"`), never sent to the page view, which would run it there.

## Behaviors worth knowing

- **Reconnect.** After the socket reconnects the client mounts the page view and every lazy view again; each starts a new VDOM cursor with its mount reply.
- **Same class twice.** Two lazy views of one class on one page are two views: each has its own state and its own cached Rust view.
- **Events with no element.** An event sent from code (`djust.handleEvent("name")`) has no container to belong to and goes to the page view. Events that come from a `dj-*` attribute in a lazy view carry its address.
- **After `live_redirect`.** `live_redirect` replaces the views on the socket and swaps the page's content without initializing the page again, so the destination's own `dj-lazy` containers do not hydrate until a full or TurboNav load.
- **Time travel, the debug panel and bug-capture replay** work on the page view (or, on a batch-only socket, the last view); they are developer tools, not part of the live contract.
