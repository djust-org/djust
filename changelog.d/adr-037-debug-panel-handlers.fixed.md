- **The debug panel lists `@staticmethod` event handlers.** Its handler list
  was a second `dir()`/`getattr` walk that missed them. It now shows exactly the
  handlers dispatch resolves (ADR-037). Pinned in
  `python/djust/tests/test_adr037_shared_discovery.py`.
