- **The WebSocket consumer and the SSE session share one error-frame builder
  (#3500).** `djust.security.build_error_frame(error, **context)` builds the
  `{"type": "error", ...}` frame both transports' `send_error` sends, so
  debug fields are handled in one place. `debug_detail`, `hint` and
  `_exc_info` are never copied onto the frame as given; under `DEBUG` the
  builder adds `debug_detail`, `hint` and an abbreviated `traceback`. SSE
  error frames now carry those `DEBUG`-only fields as WebSocket frames
  already did.
  `python/djust/tests/test_error_frame_transport_parity.py` pins identical
  frames from both transports with `DEBUG` on and off.
