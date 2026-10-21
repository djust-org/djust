- **`cancel_async_on_disconnect = True` cancels a legacy root's `start_async`
  work when its socket or SSE stream closes.** By default that work still runs
  to completion and its result is discarded (#3234), which suits work that must
  finish; work that only serves the open page (a follower, a live feed) kept
  running for a page nobody could see, and apps cancelled it themselves from
  `disconnected()`. Navigation does not cancel it, and explicit-exposure views,
  which are always disposed with their work, are unaffected. The LiveView
  reference no longer says that every view's `start_async` tasks are cancelled
  on disconnect. 2 cases in
  `python/djust/tests/test_legacy_async_cancel_on_disconnect.py`, plus WebSocket,
  navigation and SSE cases in `test_legacy_navigation_waiters_3236.py`.
