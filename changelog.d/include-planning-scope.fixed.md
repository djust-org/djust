- **A page with a dynamic `{% include %}` no longer fails on an include it does
  not render.** Such a page goes through lazy-container planning, which walks
  every `{% include %}` in the template tree, outside the `{% for %}` and
  `{% if %}` around it. It resolved each include's `with` bindings and operand
  against the page context and raised on failure, so
  `{% for r in rows %}{% include "card.html" with fields=m|f:r.key %}{% endfor %}`
  500ed with "argument `r.key` does not resolve", and so did an include behind a
  false `{% if %}` whose binding, operand or template could not be resolved or
  parsed. Django resolves these only when the include renders. Planning now
  treats what it cannot resolve as unknown and tracks provenance for that
  render; a real failure still comes from the render itself.
