- **Restoring saved state no longer replaces a view method of the same name.**
  The HTTP event fallback (and the socket snapshot, sticky-child, lazy-child and
  client-snapshot restores) wrote every saved context key back onto the view
  with `setattr`. A context key named like a method of the view
  (`ctx["images_go_out"] = self.images_go_out()`) replaced the method with the
  saved value, and the next render failed with
  `TypeError: 'bool' object is not callable`. All five restores now go through
  one helper that skips a key naming a method (with a warning, once per view
  class and key) or a property of the class. Covered in
  `python/djust/tests/test_http_fallback_restore_shape.py`.
