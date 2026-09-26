- **MCP `scaffold_view` generates a `ModelFormMixin` edit view (`features="form_edit"`, ADR-037 D2).**
  The view edits one record the signed-in user owns: `get_queryset()` filters on
  `owner`, so `djust.S013` has nothing to report. The generated code runs in the
  test suite (`python/djust/tests/doc_scenarios/generated.py`).
