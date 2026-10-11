- **A live view whose `mount()` raises is no longer left mounted.** An
  exception from `mount()` (or the state restore, `handle_params()`,
  `connected()` or the first render) used to answer with an error frame and
  keep the view as the connection's mounted view, so later events still ran
  its handlers, and it stayed in its channel groups with its presence
  tracked. Such a mount now ends like a refused one on WebSocket and SSE: a
  `PermissionDenied` is answered as the authorization check answers it
  (`code: "permission_denied"`; the page view's socket closes with 4403, a lazy
  or batched view gets `view_refused`), any other exception keeps its error
  frame, and in both cases the view is released, its groups are left, its
  presence is untracked and its tick stops. A view whose object permission is
  refused after `mount()` also untracks its presence. The HTTP fallback and
  the HTTP API already refused such a request.
