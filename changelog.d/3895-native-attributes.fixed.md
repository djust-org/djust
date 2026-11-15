- **A value marked safe for text is now always escaped when written into an attribute by the
  native component handlers (#3895).** `form_array` (an input's `value`) and `data_table` (export
  and import formats, bulk-action keys, computed-column keys, filter and expression inputs, row
  classes, footer aggregation types, and the row and column keys) used `conditional_escape` for
  those attribute values, which keeps a marked-safe member, such as one in a tuple operand, as
  it is. New cases in `python/djust/tests/test_native_attr_escape_marked_safe_3895.py`.
