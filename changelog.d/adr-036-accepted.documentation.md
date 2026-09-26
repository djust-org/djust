- **Typed event parameters are documented and ADR-036 is accepted.** The
  events guide gains a "Typed event parameters (strict policy)" section: how
  to opt in, typed click and form examples, what the browser sends, the
  conversion rules, rejections, framework context and a migration checklist.
  The AI events reference leads with the strict form. Both sets of examples
  are executed by `python/djust/tests/test_adr036_documented_examples.py`
  and `tests/js/adr036_documented_examples.test.js`. The strict policy is a
  supported opt-in; legacy remains the default.
