# Changelog — djust-theming

## [0.3.0] - 2026-02-23

### Added
- Initial release extracted from djust monorepo demo project
- `theme.css` — CSS variable design tokens for dark/light mode (`[data-theme]` attribute switching)
- `components.css` — Themed component styles (navbar, buttons, cards, badges, code blocks)
- `utilities.css` — Utility classes and framework overrides (Tailwind/Bootstrap theme-aware)
- `theme-switcher.js` — Lightweight JS for dark/light toggle with `localStorage` persistence and system preference detection
- Django app integration: add `djust_theming` to `INSTALLED_APPS` to serve static files via `{% static %}`
