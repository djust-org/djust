- **Hot view replacement: a saved view or template reaches the next page
  load.** With DEBUG and watchdog, an edit to a view's `get_context_data` and
  its template was not served to a new request until a restart. The URLconf's
  `as_view()` callables now follow the reloaded class; a template-only edit
  drops the template hash that kept a session on the renderer of the old
  template; an editor's atomic save (temp file renamed over the original) is
  seen under the original's name; and every file saved in one debounce window
  is handled, not only the first. Cases in
  `tests/unit/test_hvr_http_and_watcher.py`.
