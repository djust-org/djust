- **A WebSocket `live_redirect` to another view class now updates the page
  container's `dj-view`.** The container kept the old view path, so the next
  reconnect (which mounts from `dj-view`) mounted the old view at the new URL.
  The SSE mount already did this; the WebSocket mount now does too, for the page
  view only. 3 cases in `tests/js/ws-mount-dj-view-follows-view.test.js`.
