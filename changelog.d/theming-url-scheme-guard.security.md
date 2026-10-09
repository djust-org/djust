- **Theming URLs use a shared script-scheme denylist (backported from #3400, #3407, #3420).**
  Data-derived URLs in navigation, breadcrumbs, pagination, avatars and page
  tags are neutralised to `"#"` for `javascript:`, `vbscript:` and `data:`
  schemes, including whitespace and control-character obfuscation. Image
  sources retain `data:image/*`; links do not. Other schemes, including `sms:`,
  `geo:` and app deep links, remain unchanged. Developer-supplied URL keyword
  arguments (button `href=`, and `href`/`src`/`action`/`formaction` passed to
  the button, input, select and progress tags) raise `ValueError`.
  Navigation covers dictionary and object items, iterable collections and
  sidebar sections without mutating caller-owned items. Pagination checks
  every page, first, last, previous and next URL after formatting.
