- **A resolved `"007"` stays `"007"` in a `TagHandler` tag (#3883).** `TagHandler._resolve_arg`
  read any digit string as an integer, so `{% djust_markdown v %}` with `v = "007"` rendered `7`.
  Only the canonical spelling of an integer is read as one.
