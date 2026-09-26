- **The staged strict collector refuses `_`-prefixed `dj-value-*` names.**
  They match the server's reserved-name rule for strict parameters (ADR-036
  D5), so the browser rejects them before sending instead of relying on a
  server rejection. A 43-row conversion and binding matrix now runs
  identically through every server path in
  `python/djust/tests/test_strict_transport_parity.py`: the shared runtime,
  real WebSocket (normal and actor), real SSE, both HTTP-fallback shapes, the
  exposed API and the test client.
