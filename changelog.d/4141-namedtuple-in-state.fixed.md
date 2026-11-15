- **A namedtuple held in LiveView state keeps its field names in the template (#4141).**
  `normalize_django_value` turned every tuple into a list before the template saw it, so
  `{{ cols.0.key }}` was empty and a `{% data_table %}` whose columns are namedtuples drew them as
  lists once the view rendered from its state. It now rebuilds a namedtuple over its normalized
  members (the one rebuild the template serializer already used for #3849, shared as
  `rebuild_namedtuple`), so the page reads its fields by name and by index as Django does: on the
  first paint, after an event over the WebSocket, after a reconnect and after a fallback POST, with
  either exposure policy. Plain tuples and lists, and the session boundary, still cross as lists.
  New cases in `test_namedtuple_state_render_4141.py`.
