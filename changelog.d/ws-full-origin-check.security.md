- **The WebSocket Origin check now compares the full origin and refuses a
  missing one.** It compared only the Origin's host with `ALLOWED_HOSTS`, so a
  page on another port or scheme of an allowed host (`http://localhost:9999`
  against an app on `http://localhost:8001`) could open a socket, and a
  handshake with no `Origin` was let through. The consumer, and the new
  `djust.routing.DjustOriginValidator` that `DjustMiddlewareStack` now uses in
  place of channels' host-only `AllowedHostsOriginValidator`, accept an Origin
  only when it is the socket's own origin (scheme from the handshake or
  `SECURE_PROXY_SSL_HEADER`, host and port from `Host`, the host validated
  against `ALLOWED_HOSTS`) or is listed in the new `DJUST_ALLOWED_ORIGINS`
  setting (default: `CSRF_TRUSTED_ORIGINS`; `https://*.example.com` matches
  subdomains). A missing Origin is refused unless `DJUST_ALLOW_MISSING_ORIGIN =
  True`, for non-browser clients. The SSE event endpoints apply the same rule to
  a present Origin. **Upgrade note:** a page on one host that opens a socket to
  another (both in `ALLOWED_HOSTS`) must now list its origin in
  `CSRF_TRUSTED_ORIGINS` or `DJUST_ALLOWED_ORIGINS`; behind a TLS-terminating
  proxy set `SECURE_PROXY_SSL_HEADER`; test suites that open a Channels
  `WebsocketCommunicator` without headers need `DJUST_ALLOW_MISSING_ORIGIN = True`
  in their test settings. The `djust new` scaffold now wraps its router in
  `DjustOriginValidator`. 20 regression cases in
  `python/tests/test_websocket_full_origin.py`.
