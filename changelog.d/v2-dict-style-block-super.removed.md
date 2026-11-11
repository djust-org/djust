- **BREAKING: dict-style `block.super` reads in a Python-bridged tag.**
  Deprecated since 1.3. The lazy `block` a bridged tag receives now answers
  `block["super"]` (and `dict(block)`) with a `TypeError` naming
  `block.super()`, as Django's `BlockNode` does, and no longer has `get`,
  `values` or `items`. None of these renders the parent. Use
  `context["block"].super()`. Django's own resolution of `block.super` is
  unchanged.
