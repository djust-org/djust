- **`{% dj_suspense await=v %}` no longer reads a variable's value as the names to await
  (#3882).** The names were looked up in the context, so a value chosen at render time could pick
  which entry gated the body and whose failure message was printed. The awaited names are the
  template's own words (`await="metrics"`, as documented); a resolved `await=` renders the
  fallback and logs a warning. A resolved
  `fallback=` is not used as a template name either (the default fallback is shown and a warning
  logged), and a resolved empty `await=` no longer renders the body ungated.
