- **Native event binders honour the staged ADR-036 strict parameter
  policy.** For a strict handler, `dj-click`, the form directives, keyboard,
  paste, polling, scoped window/document, click-away, shortcut, mouse,
  `dj-mounted`, form-recovery, dropdown-observation, JS `push` and
  `dj-viewport` bindings send `dj-value-*` arguments, parsed strictly, plus
  only the generated values (`value`, `field`, form fields, `key`...) the
  handler declares, or all of them for a `**` catch-all. `_target` is not
  sent under strict. A malformed typed literal, a wire hint the declared type
  rejects, a `dj-value-*` key reusing a generated name, or a value given both
  positionally and by name is rejected in the browser. That happens before
  any lock, disable-with, optimistic or loading effect, through the existing
  value-free `djust:error` path. Legacy handlers keep their exact payloads.
