- **Service-worker caches now expire and are cleared on identity change or
  logout (ADR-038 D-n).** The worker's state-snapshot lookup enforces the
  snapshot max age (`DJUST_STATE_SNAPSHOT_MAX_AGE`, sent on the mount frame as
  `state_snapshot_max_age`; default 3600s) and deletes expired entries on read.
  Every mount frame now carries `sw_identity`, an HMAC digest of the session
  key and user id keyed on `SECRET_KEY` (`djust.security.service_worker.identity_marker`;
  never the raw values). When it differs from the one the client stored, or
  disappears, the client clears the state, VDOM and shell caches before caching
  anything from the new mount.
