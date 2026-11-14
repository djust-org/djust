- **`{% data_table %}` accepts columns given as bare names on its first render
  too (#3895).** `columns=["name", "age"]` was drawn by the native handler but
  raised `VariableDoesNotExist` in the Python tag (the page's first render,
  under Django's engine). A bare name, or a number, is now the column
  `{"key": name, "label": name}` on both engines. The components guide no
  longer says a `None` or missing URL value still draws a link, and three
  remaining differences (NaN, a `mark_safe` cell, a model-instance row) are
  pinned for a decision. New cases in
  `python/djust/tests/test_data_table_bare_columns_3895.py` and
  `python/djust/tests/test_data_table_residuals_3895.py`.
