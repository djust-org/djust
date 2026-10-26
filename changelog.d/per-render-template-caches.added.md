- **`djust.T026`: two template libraries the project loads register one tag or
  filter name.** djust's Rust engine keeps one registry for the process, so
  each `{% load %}` of either library took the name from the other and every
  cached template was parsed again; a template rendered whichever version
  loaded last. `manage.py check` now reports each such name (libraries a
  project template loads, one module under two labels counted once), and djust
  logs a warning the first time a `{% load %}` takes a name from another
  library.
