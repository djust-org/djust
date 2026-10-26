- **`LiveView.get_render_only_context()`: template values that render but are
  never saved.** Its models, QuerySets and model lists go through the JIT like
  public attributes (only the template's paths, `djust_exclude_fields`
  applied), and no session save keeps them: not the HTTP GET's, not the
  page-POST fallback's, not a socket turn's. Until now the mechanism existed
  only as the private `djust.forms` hooks `_djust_render_only_context` /
  `_djust_render_only_context_keys`. 9 regression cases in
  `python/djust/tests/test_render_only_context.py` cover this and the change
  below.
