- **CodeQL follow-up sweep.**
  - The near-miss parameter warning sanitises the parameter name at the log call
    again. Since #3095 the hot path skips `sanitize_for_log` for short ASCII
    identifiers, which cannot carry CR/LF, but the log call now does not rely
    on that.
  - `djust.websocket` no longer rebinds an unused `SessionActorHandle`
    (`live_view` imports it from `_rust` directly), and lists
    `create_session_actor` in `__all__`.
  - Documented an intentional `except ChannelFull: pass` in the in-memory
    channel layer.
