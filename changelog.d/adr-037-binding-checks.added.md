- **`manage.py check` compares template event bindings with their handlers
  (ADR-037): `djust.T019`–`T022`.** Each LiveView and LiveComponent template
  is compiled, not rendered, with `{% extends %}` and constant `{% include %}`
  followed. Every literal `dj-*` binding is checked against its owner using the
  runtime's own handler discovery, parameter policy and strict contract:
  - `T019`: a missing, undecorated or wrongly owned handler;
  - `T020`: missing, unexpected or duplicated arguments;
  - `T021`: literals and wire hints the handler rejects;
  - `T022`: routing context in markup.

  All four are Warnings in 1.3. `djust_check --format json` adds a `coverage`
  object (checked, dynamic and unsupported bindings, with gaps). Its binding
  findings carry `owner`, `binding`, `expected` and `supplied`. Suppression is
  local and needs a reason: `{# noqa: T019 -- <reason> #}`. 17 cases in
  `python/djust/tests/test_adr037_binding_checks.py`.
