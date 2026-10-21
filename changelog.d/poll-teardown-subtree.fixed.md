- **A `dj-poll` timer now stops when its element leaves the page.** Removal
  paths cleared the interval only on the node they replaced, never on a
  `[dj-poll]` inside it, so a poll nested in a page view kept firing after a
  `live_redirect` mounted another view (the server logged "Handler not found"
  on every tick). One teardown helper now stops the polls of everything a
  mount, morph, VDOM `Replace`/`RemoveChild`/`RemoveSubtree` patch, view-slot
  mount, `mount_batch` or stream op removes. Each bind pass also stops polls
  whose element left the document or dropped `dj-poll`, and a poll stops itself
  if it fires while detached. An unchanged poll on a surviving element still
  keeps its phase (#2858). New cases in `tests/js/poll_teardown.test.js`.
