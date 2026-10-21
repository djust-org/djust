- **`re_path()` LiveView routes no longer corrupt the derived client route
  map.** A regex leaf entered the map as its raw text; a catch-all
  `re_path(r"^", NotFoundView)` became `"/"` and, listed after `path("")`,
  replaced the home page, so `dj-navigate` to `/` mounted the 404 view. A regex
  route now enters the map only as a plain path whose named groups match one
  segment (`(?P<slug>[-\w]+)` → `:slug`); anything else (`.*`, alternation, a
  bare `^`) is left out, and the first pattern for a client path wins, as in
  Django's resolver. 10 cases in
  `python/djust/tests/test_route_map_regex_routes.py`.
