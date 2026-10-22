- **An `{% include %}` inside a `{% tab %}` (or another raw-body tag such as
  `{% split_pane %}`) renders on the Rust engine.** The body of such a tag is
  compiled with Django's own parser, which djust built without a template
  origin, so every `{% include %}` in it raised `'NoneType' object has no
  attribute 'template_name'`. The parser now carries the origin Django gives a
  template built from a string. An include of a name renders as Django renders
  it. A relative name (`"./x.html"`) fails as it does in a Django string
  template, because the enclosing template's name does not reach the body.
