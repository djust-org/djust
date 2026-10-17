- **Nine rarely used built-in filters run Django's own function.**
  `filesizeformat`, `get_digit`, `iriencode`, `linenumbers`, `slugify`,
  `unordered_list`, `urlize`, `urlizetrunc` and `wordwrap` now call
  `django.template.defaultfilters.<name>` instead of a Rust port. They stay
  built-ins (no `{% load %}`, the same parse-time checks), and their output,
  escaping and exceptions are Django's own: a bad argument to `wordwrap` or
  `urlizetrunc` raises Django's `ValueError`, and a huge value now renders in
  `filesizeformat` where the port gave `0 bytes`. Each call costs a Python
  call per value. About 1,500 lines of Rust are gone, including the
  `textwrap` and `urlquote` modules.
