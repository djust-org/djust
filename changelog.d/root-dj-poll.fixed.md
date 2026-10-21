- **`dj-poll` on the view's root element (`<div dj-root dj-poll="...">`) now
  polls.** The bind pass scanned only the root's descendants. Because a mount
  replaces only the root's content, the mount frame now carries the root's own
  `dj-poll` / `dj-poll-interval` (`root_poll`, `{}` for none), and the WebSocket
  and SSE clients apply it to the root: a live navigation gives the root the
  destination view's poll and stops the departing view's. Cases in
  `tests/js/poll_root_and_child.test.js` and
  `python/djust/tests/test_root_poll_mount_frame.py`.
