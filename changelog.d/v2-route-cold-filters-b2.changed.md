- **Ten more rarely used built-in filters run Django's own function.**
  `addslashes`, `capfirst`, `linebreaks`, `linebreaksbr`, `make_list`,
  `phone2numeric`, `striptags`, `truncatechars_html`, `truncatewords_html`
  and `wordcount` now call `django.template.defaultfilters.<name>` instead of
  a Rust port. They stay built-ins (no `{% load %}`, the same parse-time
  checks), and their output, escaping and exceptions are Django's own.
  `striptags` now follows the running interpreter's `html.parser`, as
  Django's does, instead of a pinned transcription of it, and an input past
  Django's stripping-depth guard raises `SuspiciousOperation` where the port
  rendered `""`. `phone2numeric` on a non-string raises Django's
  `AttributeError` itself. About 2,300 lines of Rust are gone, including the
  `htmlparser` module.
