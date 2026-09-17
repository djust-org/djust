- **Fix URL-pattern kwargs arriving percent-encoded, which made a LiveView's
  page visible but inert.** `django.urls.resolve()` expects a path that has
  already been decoded — Django decodes `request.path` before the resolver ever
  sees it — but the mount path resolves the browser's `location.pathname`,
  which is encoded. A URL like `/storybook/category/Core%20UI/` therefore
  handed `mount()` the literal string `"Core%20UI"`. Where the view validates
  that kwarg against a lookup (the storybook category pages do) the mount died
  with `Http404` while the HTTP GET rendered normally: the page displayed
  correctly and every `dj-click` on it did nothing, with the only clue an error
  in the browser console. Anything with a space, a slash or a non-ASCII
  character is affected. All three `resolve()` call sites that take a
  client-supplied URL now `unquote` first — `runtime.py:_resolve_url_kwargs`
  and the two `live_redirect` sites in `websocket.py`. The second of those feeds
  `request.resolver_match.kwargs`, which sticky views' `check_permissions` reads
  for object-level checks, so an encoded kwarg there meant an authorization
  decision taken against the wrong identifier. `unquote` is idempotent for
  paths that were never encoded.
