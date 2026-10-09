# Attribute-form lazy views over HTTP

A legacy LiveView page can defer another legacy view with authored markup:

```html
<main dj-root>
  <div dj-view="myapp.views.Counter" dj-lazy="click">Load first counter</div>
  <div dj-view="myapp.views.Counter" dj-lazy="click" class="{{ extra }}">Load second counter</div>
</main>
```

With WebSocket and EventSource disabled, each registered container fills through
an event POST to the page URL. Subsequent events address that same child and
retain its public and private legacy state independently. This also works for
containers emitted by loops, conditionals, includes and inherited blocks.
`{% live_render lazy=True %}` keeps its existing rendering and fill protocol.

The child class must pass the `live_render` resolver and its module allowlist.
The server supplies an opaque, keyed `data-djust-lazy-id` and
`data-djust-embedded` address. The browser posts the address, never a class or
restored state. The address binds the page class, URL, session, authenticated
user, parent and child tenant scopes, and occurrence of the class in the page.
A fresh page render must authorize that address on every child POST. Mount and
object permissions run before event dispatch; object permissions run again
after restoring server-held state. Permission refusals use the existing HTTP
error envelope. Explicit-exposure views continue to require their transport's
mount binding, as on the existing embedded HTTP path.

## Render provenance

`djust_templates::provenance::Rendered` owns the exact HTML and sorted, disjoint
UTF-8 byte intervals for its authored literals. The same generic renderer emits
ordinary `String` results or tracked results. Every composition site shifts the
child intervals by the current output length. Loop iterations, conditional
markers, include origins, inheritance overrides and unfiltered `block.super`
compose their own results. There is no ambient provenance collector, reset/take
API, or thread-local record. Nested render calls cannot grant authority to an
outer render's value output.

Values, custom-tag return values, filters, escaping, spaceless transforms and
captured/reinjected `|safe` HTML grant no authored intervals. A filter also drops
intervals when its output is byte-identical. Unfiltered `block.super` is direct
renderer composition; filtered or captured `block.super` is a value.

Python page assembly carries the sideband in `RenderedHTML`. Slices clip and
shift UTF-8 intervals; concatenation shifts them; replacements invalidate the
bytes replaced. Whitespace normalization applies to tracked output, not to its
source before authority is recorded. Its exact Rust deletion ranges preserve
the intervals of untouched bytes. Converting the result to ordinary text and
reinserting it, including via a wrapper-template value, drops that authority.

Provenance alone does not authorize a container. `djust_vdom` parses the final
coherent page with the HTML5 tokenizer and tree builder. It correlates the
selected attribute ranges with actual start-tag tokens and actual live elements
using a parser-private namespaced annotation that never enters the page output.
Comments, script/style bodies, RCDATA, escaped text, inert template contents,
quoted attribute strings and ignored/merged tags cannot authorize a child.
The selected `dj-view` and `dj-lazy` attributes, including the trigger, must be
fully authored. Duplicate attributes follow HTML5's first-attribute selection.
Unrelated interpolated attributes need no authority.

Ordinary render APIs remain monomorphized `String` paths, with loop caching
unchanged. Python selects tracking for source containing lazy markup or possible
include/inheritance composition; this conservative selection can also track an
include page whose resolved output has no lazy container. The final HTML5 check
runs only for tracked output with lazy markup.

## Verification

The contract matrix is in
`python/djust/tests/test_lazy_provenance_http_3252.py`; HTTP client routing is in
`tests/js/view_slots_3252.test.js`. The self-contained real-browser regression is
`tests/playwright/test_lazy_attribute_http_3252.py`:

```bash
.venv/bin/python tests/playwright/test_lazy_attribute_http_3252.py
```

It pins WebSocket and EventSource off, verifies two independent counters and a
separate browser session, and asserts that the requests actually use HTTP.
`CHROMIUM_EXECUTABLE` may select an installed Chromium browser.
