- **Startup checks for the staged ADR-036 strict parameter policy.** A
  strict-policy declaration that strict dispatch would reject is now reported
  by `manage.py check`, naming the view, the handler and the parameter:
  `djust.C021` for an invalid `event_parameter_policy`, `djust.V016` for an
  unresolvable or unsupported annotation, a missing annotation or a reserved
  argument name, `djust.V017` for an async strict handler on an actor view, and
  `djust.V018` when `params=` disagrees with a strict signature. The checks
  compile the same cached contract dispatch uses. Deferred annotations now
  resolve against the defining class body before module globals, and strict
  contracts reject keyword parameters named `view_id`, `component_id` or
  starting with `_`, which no transport can deliver. V007's "add `**kwargs`"
  advice no longer applies to strict handlers. Legacy-policy handlers, still
  the default, report nothing new. 32 collected cases in
  `python/djust/tests/test_parameter_contract_checks.py`.
