---
table: python
covers: djust.ReactMixin, djust.react_components, djust.register_react_component
removed: `djust.react` (`ReactMixin`, `react_components`, `register_react_component`) and capitalized `<Button />`-style component tags in templates
use: A JavaScript hook: `dj-hook="Name"` on an ordinary element, the hook in `window.djust.hooks` ([hooks guide](hooks.md)). Importing the names raises `ImportError`; a capitalized tag that is not an uppercase HTML element name is a template error ([#4006](https://github.com/djust-org/djust/issues/4006)).
---
