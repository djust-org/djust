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
user, parent and child tenant scopes, and the authored container node (template
relative name, authored container index and a short hash of its start-tag bytes)
plus its include-site and loop index paths. Include sites name their defining
template (or `inline` for the inline page template), including sites moved
through inheritance.
Hiding an earlier conditional container does not change the remaining child's
address or state. Removing a loop item changes subsequent index paths; use a
stable ordered input when retaining loop child state. Old occurrence-based
addresses and addresses for containers absent from the current render are
refused. Absolute checkout paths and unrelated template text do not enter the
address: pods with the same template names and container bytes share identities.
Changing container bytes changes its address; the old address is refused.
A fresh page render must authorize that address on every child POST. Mount and
object permissions run before event dispatch; object permissions run again
after restoring server-held state. Permission refusals use the existing HTTP
error envelope. Explicit-exposure views continue to require their transport's
mount binding, as on the existing embedded HTTP path.

HTTP child event POSTs instantiate the child and call `mount()` before restoring
its saved public and private state, matching the existing HTTP embedded-child
path. Mount side effects therefore run on every event POST; this differs from
a WebSocket child's lifetime. Wrapper-template pages do not issue attribute-form
lazy addresses because reinjecting the page as a wrapper value drops provenance.

## Render provenance

`djust_templates::provenance::Rendered` owns the exact HTML and sorted, disjoint
UTF-8 byte intervals for its authored literals, with stable authored node
addresses. The same generic renderer emits
ordinary `String` results or tracked results. Every composition site shifts the
child intervals by the current output length. Loop iterations, conditional
markers, include origins, inheritance overrides and unfiltered `block.super`
compose their own results. There is no ambient provenance collector, reset/take
API, or thread-local record. Nested render calls cannot grant authority to an
outer render's value output.

Values, custom-tag return values, expression literals, filters, escaping,
spaceless transforms and captured/reinjected `|safe` HTML grant no authored
intervals. A filter also drops
intervals when its output is byte-identical. Unfiltered `block.super` is direct
renderer composition; filtered or captured `block.super` drops start-tag
authority. Flattening a possibly-open authored literal context records the
cutoff described below.

Python page assembly carries the sideband in `RenderedHTML`. Slices clip and
shift UTF-8 intervals; concatenation shifts them; replacements invalidate the
bytes replaced. Whitespace normalization applies to tracked output, not to its
source before authority is recorded. Its exact Rust deletion ranges preserve
the intervals of untouched bytes. Converting the result to ordinary text and
reinserting it, including via a wrapper-template value, drops that authority.

At compile time, each container must be live in its own authored template
context, with template expressions masked. The complete tracked Rust render
then checks liveness again using the literal bytes emitted by the selected
branches and loop iterations. Its context includes quoted variable expressions,
the selected literal operand of `cycle`/`firstof` (including named bindings),
and literal `with`/include bindings and aliases. Literal-derived expression
output participates after escaping and built-in filters whose input and arguments
are literal-derived. Custom filters and the environment-dependent built-ins
`date`, `time`, `timesince`, `timeuntil`, `floatformat`, `filesizeformat`,
`yesno`, `truncatechars`, `truncatechars_html`, `truncatewords` and
`truncatewords_html` remain opaque, as do translated `_()` expressions and environment-dependent
tag output such as `now` and `trans`. A value-derived filter chain remains
opaque even if a filter selects a literal fallback argument. These
context bytes never grant start-tag authority.
Unfiltered `block.super` composes its literal context; filtered `block.super`
participates as literal-derived only when the entire parent output is literal
and its filters meet the same rule. Built-in filters meeting that rule that
leave a mixed parent output byte-identical retain its literal/opaque context
boundaries, while dropping all start-tag authority. This prevents a filtered
parent from coalescing a literal opener and a value closer into one balanced run.
Whenever output carrying authored-literal provenance is flattened, the renderer
scans its authored literal bytes with value and renderer-owned marker bytes
blanked at the same offsets. A fail-closed cutoff is recorded iff those bytes
end in a possibly-open inert context: an unclosed comment, raw-text or
escapable-raw-text element (`script`, `style`, `textarea`, `title`, `xmp`,
`iframe`, `noembed`, `noframes`, `noscript`, `plaintext`), unfinished start/end
tag, quoted attribute value, CDATA or bogus comment. The small scanner reports
open whenever uncertain (including unsupported foreign/tree-builder contexts).
Pure-literal runs are covered; mixed runs ending in data state do not cut off.
No lazy container after a cutoff in the complete render is authoritative, even
after a literal closer. An inner cutoff always survives, including empty output.
This rule applies at every routed flatten site: filtered `block.super` when
boundaries cannot be preserved, `{% filter %}`, `{% spaceless %}`, with/include
bindings and other value captures, and custom block-body captures.
Cache fragments retain their cutoff as metadata on the cached string itself,
stored atomically under Django's usual fragment key for hits and misses. Plain
legacy entries, untracked overwrites and backends stripping that metadata are
uncertain and fail closed. Fragment text and Django's cache key remain unchanged.

Only-value content has no authored opener and does not acquire a cutoff.
Byte-identical literal-derived filters of `block.super` preserve its context
boundaries (but drop start-tag authority) and therefore do not flatten it.
Literal-only byte-identical filters keep following-container authority.
Renderer-owned neutral marker bytes do not count as values. These rules concern
rendered content with provenance; ordinary view values and expression operands
have no authored-render sideband, as described below.

Liveness is computed on authored text (template literals and literal expression
output) in the rendered branch. Non-literal output, including view-context data,
framework tags and HTML-producing filters applied to values, is replaced by
whitespace at the same byte offsets. Such output does not break authority for
following containers merely because it contains `<`, `>`, quotes or `--`.

A container whose hiding context is produced by a non-literal value (view-context
data), or assembled across literal/value boundaries, is not modelled by this
liveness computation. Authors must not hide lazy containers with values. For
example, a value emitting `<!--` followed by a value emitting `-->`, or
`<{{ tag }}>` with `tag="script"`, can leave a following authored container
registered when it survives in the final page. An expression such as
`{{ "<!--"|add:value|safe }}` is also opaque because its filter argument is a
value, even though its input is quoted literal text; a value can close that
expression's opener. The final-page (E) survival check
still rejects containers that are actually hidden in the final HTML; it does not
track their earlier value-generated hiding context. Conversely, a literal
`{{ "<!--" }}` cannot be closed by a non-literal value for authored-text
liveness, even if that value makes the container visible in the final page.

Literal context and opaque runs compose across includes, inheritance and loop
iterations. This check is applied once to the complete render. An authored
closer in a skipped branch cannot make a following container authoritative;
a value cannot supply the closer for an inert context opened by literal text
or literal-derived expression output. Only containers live in both the
compile-time and rendered checks keep start-tag authority and origin addresses.
The compile-time check remains conservative: a container rejected there does
not gain authority just because a particular rendered branch would be live.

Provenance alone does not authorize a container. `djust_vdom` parses the final
coherent page with the HTML5 tokenizer and tree builder. It correlates the
selected attribute ranges with actual start-tag tokens and actual live elements
using a parser-private namespaced annotation that never enters the page output.
Comments, script/style bodies, RCDATA, escaped text, inert template contents,
quoted attribute strings, noscript bodies and ignored/merged tags cannot authorize a child.
The selected `dj-view` and `dj-lazy` attributes, including the trigger, must be
fully authored. Duplicate attributes follow HTML5's first-attribute selection.
Unrelated interpolated attributes need no authority.

Ordinary render APIs remain monomorphized `String` paths, with loop caching
unchanged. Compile-time analysis checks authored tag/attribute ranges, and
render planning follows resolved includes and inheritance parents. A dynamic
template reference preserves raw source until its target can be resolved;
tracking is enabled only if that resolved tree contains an authored container.
The final HTML5 check runs only for tracked output with lazy markup.

## Verification

The contract matrix is in
`python/djust/tests/test_lazy_provenance_http_3252.py` and
`python/djust/tests/test_lazy_expression_literals_3430.py` and
`python/djust/tests/test_lazy_context_state_3442.py`; HTTP client routing is in
`tests/js/view_slots_3252.test.js`. The self-contained real-browser regression is
`tests/playwright/test_lazy_attribute_http_3252.py`:

```bash
.venv/bin/python tests/playwright/test_lazy_attribute_http_3252.py
```

It pins WebSocket and EventSource off, verifies two independent counters and a
separate browser session, and asserts that the requests actually use HTTP.
`CHROMIUM_EXECUTABLE` may select an installed Chromium browser.

Tracking is planned from compiled literal nodes and resolved includes/parents;
ordinary pages use the plain renderer. Python normalization projects intervals
to potential lazy start tags, applies the frequent whitespace edits in linear
Rust passes, and batches other composition in one offset pass. Slices use a
cached sparse UTF-8 index. The final HTTP page registers containers once, after
all page edits, so one HTML5 survival check serves GET or child POST. Direct
registration results retain their validation for identical-result finalization.

The parser token must open at the candidate's authored start and select exactly
the authored `dj-view` and `dj-lazy` name/value bytes. Class and trigger come from
those authored bytes. Candidates containing another raw `<` fail closed, even
inside a quoted attribute; HTML entities or newline normalization that change
an authority attribute's selected bytes also refuse registration.

The template-tree planner detects include cycles and memoizes visits, with a
20-include depth limit. Plans are cached by template, registry generation and
loader directories; resolved dynamic plans also depend on the context paths
consumed by include/parent selectors (including filter arguments and indexed
paths). Per-walk memo entries use the same selector inputs. Nested include
bindings are rebased into the caller's context, and `only` isolates unbound
names. Bindings such as `rows=rows` are omitted from these keys when no nested
selector consumes them. A selector that actually consumes a large value still
pays for that value's fingerprint; unrelated render state does not.
Dependency mtime/length checks use the template loader's invalidation policy,
including missing candidates that could later become selected.

Some authored lazy markup intentionally fails closed and receives no address:
dynamic includes selected by a loop variable or a `{% with %}` binding that
is absent from the planning context; source/render context mismatches (for
example a conditional comment opener); and containers beyond 20 includes.
Top-level context selectors such as `{% include chosen %}` are supported.
The `djust._lazy_containers` logger emits a debug message when lazy markup
remains unregistered, with context, selector, depth and authority checks to try.
This diagnostic also covers permission refusals and inert/untrusted markup;
it does not grant authority or expose interpolated values.

Unauthored whitespace immediately after a valueless `dj-lazy` belongs to its
selected attribute range and fails closed. Prefer a fully literal attribute,
or an explicit quoted trigger, and keep interpolations outside authority bytes.
