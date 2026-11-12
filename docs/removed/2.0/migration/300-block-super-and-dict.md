---
table: templates
removed: `block["super"]` and `dict(block)` inside a custom tag that receives `block`
use: `context["block"].super()`
---
