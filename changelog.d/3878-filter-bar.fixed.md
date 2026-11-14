- **`{% filter_bar %}` draws the controls a template nests in a condition under the Django
  template engine (#3878).** The Django tag read only the DIRECT child nodes of its body, so a
  `{% filter_search %}` inside `{% if %}` / `{% for %}`, a bar inside the bar and any other
  content were dropped, where djust's own engine drew them. The bar now renders its body: every
  control it rendered is the bar's, and the rest is kept after the controls, as natively.
