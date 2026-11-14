- **`{% data_table %}` accepts columns given as bare names on its first render
  too (#3895).** `columns=["name", "age"]` was drawn by the native handler but
  raised `VariableDoesNotExist` in the Python tag (the page's first render,
  under Django's engine). Any column that is not a dict (a name, a number,
  `None`, a bool) is now the column whose key and label are its text, on both
  engines; that text is plain even when marked safe (markup in a header is the
  dict form's `label`). The components guide no
  longer says a `None` or missing URL value still draws a link, and three
  remaining differences (NaN, a `mark_safe` cell, a model-instance row) are
  pinned for a decision. New cases in
  `python/djust/tests/test_data_table_bare_columns_3895.py` and
  `python/djust/tests/test_data_table_residuals_3895.py`.
