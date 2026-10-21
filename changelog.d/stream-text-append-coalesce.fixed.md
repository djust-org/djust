- **`stream_text(mode="append")` no longer drops text sent inside the rate
  window.** An op that landed within ~16 ms of the last send replaced the
  stream's queued op ("only latest matters"), which is right for a replace but
  lost every earlier chunk of an append: five tight appends of `"a"`..`"e"`
  reached the client as `"ae"`. Queued text appends (and prepends) are now
  merged into one op, a replace still supersedes what is queued for its
  target, and ops for different targets of one stream are all kept. 8 cases in
  `python/djust/tests/test_stream_text_append_coalesce.py`.
