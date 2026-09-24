- **Explicit views: `url_change` is authorized fresh and persisted.** A
  staged explicit root's route change ran `handle_params` and re-rendered with
  the mount-time principal, re-checked object permission against the mount-time
  request, and never saved the declared state that `handle_params` changed. It
  now authorizes against a fresh session first, which drops a revoked turn with
  the foreground denial. The object-permission check uses that request, and
  declared server state is committed before the render frame. Legacy views are
  unchanged.
