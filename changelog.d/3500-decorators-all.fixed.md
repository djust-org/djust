- **`from djust.decorators import *` now includes `action` and `is_action`
  (#3500).** The top-level `djust` package exported both from
  `djust.decorators`, but `djust.decorators.__all__` left them out.
  `python/djust/tests/test_decorators_all_agrees_with_top_level.py` derives the
  check from the lazy-export table, so the two lists cannot drift again.
