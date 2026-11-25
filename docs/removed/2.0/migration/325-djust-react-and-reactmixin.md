---
table: python
covers: djust.ReactMixin, djust.react_components, djust.register_react_component
removed: `djust.react` (`ReactMixin`, `react_components`, `register_react_component`) and capitalized `<Button />`-style component tags in templates
use: A JavaScript hook: `dj-hook="Name"` on an ordinary element, the hook in `window.djust.hooks` ([hooks guide](hooks.md)). Importing the names raises `ImportError`; a capitalized tag is a template error unless it is an HTML element, and HTML reads element names in any case: `<DIV>`, `<TextArea>` and `<tItLe>` are elements, while a self-closing non-void element (`<Button label="x" />`, `<Header />`) and a name HTML does not have (`<Widget>`) are component tags ([#4006](https://github.com/djust-org/djust/issues/4006)).
---
