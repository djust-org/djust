- **The JIT finds the paths a template reads inside `{% tabs %}` and in the
  includes of an inline template.** A variable read only in the body of a
  raw-block tag (`{% tabs %}…{% tab %}`, `{% split_pane %}`) had no path,
  because the body reaches Python as source; `extract_template_variables` now
  parses those bodies (best effort; render-time dependency tracking is
  unchanged). An inline `template` string, and a file template with no
  `{% extends %}` once `get_template()` had run, were not given their
  `{% include %}` partials. In both cases a `list[Model]` was serialized with
  every field and none of its annotations. 15 regression cases in
  `python/tests/test_jit_template_paths.py` (with the new `template_paths()` API).
