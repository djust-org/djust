- **The AI schema's forms pattern teaches `ModelFormMixin` for editing (ADR-037 D2).**
  `get_best_practices()` no longer shows `_model_instance` set in `mount()`. It
  shows a create form and, as `edit_example`, the form guide's `ModelFormMixin`
  view. `ModelFormMixin` is listed in `optional_mixins`. Both examples run in the
  test suite (`python/djust/tests/doc_scenarios/generated.py`).
