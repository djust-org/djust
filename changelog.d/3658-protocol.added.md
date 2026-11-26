- **A wire-protocol version, and a self-reload for outdated tabs (#3658).** The
  server and the client bundle name a protocol version (`2`). The client sends it
  with every mount verb, on the SSE stream GET and in the `X-Djust-Protocol`
  header of its POSTs. A tab running an older cached bundle (1.x, or a 2.0
  release candidate) is told to reload: WebSocket close code 4409 after a
  `reload` frame, an SSE `reload` frame, or HTTP 409 `protocol_mismatch`. The
  bundle URLs carry `?v=<djust version>` so the reload fetches the new bundle. A
  second refusal of the same browser within 30 seconds sends no `reload` frame,
  so a service worker that keeps serving the old page shell cannot make the tab
  reload forever; unregister it or clear the site's data. Clients that send no
  version (scripts, hand-built test frames) can be accepted with
  `DJUST_ALLOW_MISSING_PROTOCOL = True`.
