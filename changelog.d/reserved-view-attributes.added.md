- **System check `djust.V022` reports view code that assigns an attribute djust
  keeps its own state under** (`_streams`, `_components`, `_pending_navigation`
  and the other attributes `LiveView.__init__` sets; `request` and `_object`
  are allowed). A view that kept its own lists in `self._streams` failed every
  render with `'builtin_function_or_method' object is not iterable`; the
  streams context now raises a `TypeError` that names `_streams` instead. The
  reserved names are read from a bare `LiveView`, not a hand-kept list. 5 cases
  in `python/djust/tests/test_reserved_view_attributes.py`.
