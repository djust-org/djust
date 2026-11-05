- **The server-function and sticky-LiveView demo pages printed their own
  template comments.** Five multi-line `{# #}` comments in `examples/` (Django's
  `{# #}` is single-line only, so they rendered as text) are now
  `{% comment %}` blocks. `scripts/check-template-comments.py` scanned only
  `python/djust`; it now scans `examples/` too (`--path` is repeatable) and
  skips `staticfiles/`, `node_modules/` and virtualenvs.
