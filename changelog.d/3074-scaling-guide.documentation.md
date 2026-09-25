- **New guide: "Scaling a djust Process Across Cores"
  (`docs/website/guides/scaling-across-cores.md`, #3074).** It explains
  why a stock process uses about one core, then covers the recipe:
  free-threaded CPython 3.14t and the `cp314t` wheels,
  `LIVEVIEW_CONFIG["worker_threads"]` and its event-loop offload, scoped push
  (`push_scope` / `scope=`), `djust.layers.InMemoryChannelLayer` and the
  GIL-releasing render. It includes the snake load test numbers (about 32
  clients on one core for stock 3.12, 192–256 clients on 4–6.6 cores for
  3.14t with the opt-in settings), the memory cost per session (2.2–2.7 MB
  for a pinned pool against 5.4 MB for one thread per session), and the
  Redis multi-process alternative with its trade-offs.
