- **Staged ADR-036 contracts reach HTTP pages.** A page whose view has
  strict-policy handlers now renders its public parameter contracts into a
  JSON data block outside the live root. HTTP-fallback render responses carry
  the rendered tree's contracts, the same fields as WebSocket/SSE render
  frames. The client keeps them as the page's own scope and resolves a
  native binding's owner (root, component or embedded child) and handler
  against the transport it will send through. Native binders do not consume
  the contracts yet. All-legacy pages and responses are unchanged. 10 collected
  cases in `python/djust/tests/test_http_parameter_contracts.py`.
