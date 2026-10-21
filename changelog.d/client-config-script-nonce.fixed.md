- **The injected "djust configuration" script, and the DEBUG debug-info script,
  now carry the request's CSP nonce.** Under a policy without `'unsafe-inline'`
  the browser blocked both on every page, so the client ran on its defaults and
  the debug panel had no view info. The nonce is HTML-escaped and omitted when
  the request has none. 4 cases in
  `python/djust/tests/test_client_script_nonce.py`.
