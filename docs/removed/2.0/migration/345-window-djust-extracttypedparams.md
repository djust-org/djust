---
table: templates
removed: `window.djust.extractTypedParams` and `data-*` attributes as event arguments
use: `dj-value-*` attributes (`dj-value-item-id:int="4"`); the browser sends only what the handler's signature declares ([#4205](https://github.com/djust-org/djust/issues/4205))
---
