- **Attribute-form lazy views (`<div dj-view="app.Child" dj-lazy>`) now fill
  over the page-POST HTTP fallback and keep their own state across events
  (#3252),** as `{% live_render lazy=True %}` already did. A container
  registers only when the renderer records that its tag, `dj-view` and
  `dj-lazy` bytes came from authored template source and the final page
  parses them as a live element; containers inside loops, conditionals,
  includes and inheritance, and with unrelated interpolated attributes,
  register. Escaped text, comments, script bodies and markup re-injected
  through `|safe` never do.
