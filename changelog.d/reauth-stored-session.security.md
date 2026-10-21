- **`reauth_on_event` now notices a sign-out in another tab.** The per-event
  re-check resolved the user from the socket's `scope["session"]`, the session
  object loaded at connect, whose cached data still named the user after the
  stored session was deleted; the open socket kept running events. The re-check
  now reloads the session from the configured `SESSION_ENGINE` by key before
  resolving the user, so a signed-out socket closes with 4403 and the handler
  does not run. 2 regression cases in
  `python/djust/tests/test_reauth_reads_stored_session.py` (real DB session, no
  mock of `get_user`).
