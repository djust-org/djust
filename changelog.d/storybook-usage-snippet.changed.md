- **The storybook's USAGE block shows the two files a developer writes.** It
  documented the component in isolation — `Progress(value=0, max=100,
  label=None)` followed by `component.render()` — which is not the shape of any
  real call: it showed the signature's defaults rather than the arguments that
  produce the preview above it, and it left out the `{{ component|safe }}` that
  actually puts the component on a page. It now shows the view that holds the
  state and the template that renders it.

  For a template component the snippet is the tag, with the values a view
  would hold passed by name; anything a template cannot write as a literal —
  a list of dicts, a page's nav items — is assigned on the view and passed by
  name, because `repr()` of a list is a `TemplateSyntaxError` rather than a
  value. Every snippet for a contracted component is compiled and rendered in
  the test suite, so the block whose whole job is to be copy-pasteable cannot
  ship something that raises.
