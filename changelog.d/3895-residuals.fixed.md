- **`{% data_table %}` draws the same table on both engines for the remaining cases (#3895).** The
  native handler (every render after a LiveView event) was handed `rows` and `columns` as JSON
  text, which loses four things the Python tag (the first render) sees; djust's engine now also
  hands the handler a list or dict operand that holds one of them, and both implementations read
  rows and columns through the same functions. A cell value marked safe by the developer keeps its
  markup after an event, as on first load (plain strings and lazy translation strings stay
  escaped; a lazy value resolving to a marked-safe string and a `str` subclass with `__html__` are
  still honoured only by the Django-engine render; every attribute built from a row value is
  escaped, and a URL is checked whatever it is marked); a row whose keys are not all text (`{1:
  "a", "1": "b"}`) is read with its own keys, as Django reads it; a model instance
  row is read as djust serialises it, so an excluded field is an empty cell on both, and another
  object is read for the column keys; `NaN` / an infinity is written `nan` / `inf` on both and is
  never a link; a function inside a column dict is ignored on both, with one warning naming the
  column; and a column given as an object or a namedtuple is read for its `key` / `label` /
  `width` natively too. New cases in `python/djust/tests/test_data_table_residuals_3895.py`.
