- **Data-derived theming URLs with a script-bearing scheme now render as `"#"`
  instead of raising.** The nav, nav-group, sidebar and breadcrumb item lists,
  `theme_nav_item`, `theme_pagination`'s `url_pattern`, `theme_avatar`'s `src`,
  and the auth, error and empty-state page tags take their URLs from app data,
  so one stored `javascript:` link turned every viewer's render into an error.
  They now follow the component library's policy: a disallowed scheme becomes
  `"#"` and the rest of the component renders; an avatar keeps a legitimate
  `data:image/*` source. `theme_button href=` and the `**attrs` passthrough are
  developer-supplied and still raise `ValueError`.
- Navigation item URLs are validated even when supplied as Django lazy strings
  or other URL objects. Safe values retain single template escaping and the
  caller's item dictionaries are not modified.
