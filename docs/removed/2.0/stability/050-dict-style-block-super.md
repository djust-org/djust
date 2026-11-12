### Dict-style `block.super` reads in a bridged tag → `block.super()`

Before `{{ block.super }}` became lazy, a Python-bridged tag received `block` as a
`dict` (`{"super": "<rendered parent>"}`). It now receives a lazy object with Django's
own `BlockNode` shape, whose `super()` method renders the parent when it is read.
The dict-style reads were deprecated in 1.3 and removed in 2.0.0:

- `block["super"]` raises `TypeError` (not subscriptable), with a message naming
  `block.super()`, and `dict(block)` raises the same error;
- `block.get("super")`, `block.values()` and `block.items()` raise `AttributeError`.

None of them renders the parent. Django's own resolution (`Variable("block.super")`,
`{% blocktranslate with s=block.super %}`) is unaffected: it falls through the
`TypeError` to `super()` exactly as it does for a `BlockNode`.

```python
# Before
parent_html = context["block"]["super"]

# After
parent_html = context["block"].super()
```
