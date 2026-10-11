- **The live mount checks object permission after `handle_params()`.** The
  WebSocket and SSE mounts ran `has_object_permission()` after `mount()` but
  before `handle_params()`, so a view that selects its object from the URL's
  query parameters in `handle_params()` was checked against the object
  `mount()` chose and then rendered the one the parameters selected. The mount
  now runs `mount()`, `handle_params()`, the object check, and then the
  first render: the order the HTTP GET render and
  `url_change` navigation already used. A refused mount still sends a
  `permission_denied` error frame and closes the WebSocket with 4403.
