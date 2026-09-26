- **The MCP tool `find_handlers_for_template` uses the `manage.py check`
  binding scan (ADR-037).** A view or component now matches when its template
  is the file, or includes or extends it, instead of when the file names
  match. The existing JSON keys are unchanged. Each view gains `bindings`
  (with each binding's `status` and `djust.T019`–`T022` findings), and the
  response gains a `coverage` object. 5 cases in
  `python/djust/tests/test_find_handlers_for_template.py`.
