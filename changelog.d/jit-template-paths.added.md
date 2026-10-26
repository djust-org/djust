- **`LiveView.template_paths()` and `LiveView.serialize_for_template(value,
  name)`.** The first returns the dotted paths the view's template reads, by
  name (the JIT's own extraction). The second hands the template a value by
  those paths: models, QuerySets and model lists through the JIT, any other
  object (a dataclass, a presenter) reduced to the paths read of `name`, with
  the serialization floor applied. A view no longer needs the private
  `djust.mixins.jit._cached_extract_template_variables` to give the renderer
  plain data instead of a live object. Covered by `python/tests/test_jit_template_paths.py`,
  with the path fixes.
