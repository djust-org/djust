- **BREAKING: a Rust template-inheritance or JIT-serialization failure now
  raises instead of degrading.**
  - **Template inheritance:** when `resolve_template_inheritance` fails for a
    view's `{% extends %}` template (for example a template directory missing
    from the search path), `get_template()` raises `RuntimeError` naming the
    template and the directories searched. Before, it logged a warning and the
    initial HTTP GET served the `dj-root` fragment without the base template's
    `<head>` (#1801). **Templates that hit that path now error instead of
    rendering half a page.** The JIT variable-extraction lookup raises the same
    error instead of silently using the single-file source.
  - **JIT serialization:** a failure serializing a QuerySet or model for the
    template raises `RuntimeError` (the cause is chained). Before, it logged and
    re-serialized through `normalize_django_value`, which re-ran the query and
    sent every concrete field instead of the ones the template reads. The
    `jit_serialization` setting still turns the JIT serializer off.
