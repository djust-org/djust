- **`{% djust_markdown %}`, `{% dj_flash %}`, the PWA tags and `{% live_render %}` take a resolved
  argument as the text it is (#3882).** Their shared argument reader, `TagHandler._resolve_arg`,
  looked an argument the engine had already resolved up in the template context a second time
  when it was shaped like a variable name or dotted path, so text that spelt one printed that
  value instead of the text (`{% djust_markdown comment.body %}` with a comment reading `a.b`).
  The engine is now the one resolver of an argument; an attribute the template itself names, such
  as `post.body`, is still resolved by the engine. A third-party `TagHandler` that called
  `_resolve_arg` on a bare name to read a context variable now gets the text back: read `context`
  directly.
