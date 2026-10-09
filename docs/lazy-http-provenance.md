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
authority. Flattening preserves the literal-only context described below.

Python page assembly carries the sideband in `RenderedHTML`. Slices clip and
shift UTF-8 intervals; concatenation shifts them; replacements invalidate the
bytes replaced. Whitespace normalization applies to tracked output, not to its
source before authority is recorded. Its exact Rust deletion ranges preserve
the intervals of untouched bytes. Converting the result to ordinary text and
reinserting it, including via a wrapper-template value, drops that authority.

Source annotation locates authored tag/attribute spans without deciding
liveness. The complete tracked Rust render checks liveness using the literal
bytes emitted by the selected
branches and loop iterations. Its context includes quoted variable expressions,
the selected literal operand of `cycle`/`firstof` (including named bindings),
and literal `with`/include bindings and aliases. Literal-derived expression
output participates after escaping and built-in filters whose input and arguments
are literal-derived. Custom filters and the environment-dependent built-ins
`date`, `time`, `timesince`, `timeuntil`, `floatformat`, `filesizeformat`,
`yesno`, `truncatechars`, `truncatechars_html`, `truncatewords` and
`truncatewords_html` remain opaque, as do translated `_()` expressions and environment-dependent
tag output such as `now`, `trans` and `blocktrans`/`blocktranslate`.
Raw translation bodies cross to Django as source; their translated output is
opaque, rather than a flattened tracked capture. A value-derived filter chain remains
opaque even if a filter selects a literal fallback argument. These
context bytes never grant start-tag authority.
Unfiltered `block.super` composes its literal context; filtered `block.super`
and other captured values drop start-tag authority. Captured values carry their
literal-only bytes through scoped bindings and aliases, emitted at each use rather
than at assignment. Unused captures and condition operands contribute no output.

Replay of byte-changing transforms on captured literal-only content uses an
explicit allow-list, distinct from evaluation of fully literal expressions:

- `lower`, `upper`, `title`, `capfirst`: Unicode text casing, with no locale lookup
  or truthiness-based fallback output.
- `cut`: ordinary text removal in B; in P, removal separately between U+FFFD
  separators so replay cannot delete or match across opaque or authored U+FFFD.
- `escape`, `force_escape`: HTML escaping, using the same input safety and
  autoescape policy as the page filter call.
- `linebreaksbr`: newline-to-`<br>` conversion;
  `linebreaksbr` uses the same input safety and autoescape policy as the page.
- The `spaceless` wrapper: trim and remove inter-tag whitespace in both runs.

Every replayed filter argument must be a template literal or a literal-derived
binding; value arguments are forbidden. The captured literal-only output receives
exactly the page output's final escaping decision (including attribute escaping).
No fallback, truthiness, numeric, time, random or locale/i18n-dependent filters are
replayed: in particular `default`, `default_if_none`, `yesno`, `length`,
`length_is`, `pluralize`, `add`, `date` and `time` are excluded. Unlisted transforms,
including `slice`, `striptags` and Python filters, retain literal context only if
their page output is byte-identical. A byte-changing unlisted transform over a run
containing authored bytes fails closed: its unknown literal context makes the
following containers non-authoritative. A permanent cutoff travels with the
capture through aliases, includes and cache fragments; authored closers cannot
restore authority after it. Earlier containers keep their literal projection
and still require final-page survival. Escaping that erases an authored hiding
context also establishes a cutoff. Unknown cache metadata continues to fail
closed for the complete render, as do unknown byte changes from custom block tags.

During a tracked render, the renderer composes two literal-only projections of
exactly the selected branch and loop iterations. Authored bytes and literal-derived
expression output are kept in both. Projection **B (blank)** replaces every
non-literal value occurrence and opaque tag result with the empty string.
Projection **P (placeholder)** replaces each with a single U+FFFD, even when the
page output is empty. Non-emitting tags such as `load`, template comments,
`resetcycle` and assignments are opaque results; renderer-owned neutral markers
contribute no bytes in either projection. Offset mappings use UTF-8 byte lengths.
Flattened captures carry both transformed projections, without start-tag authority.

B replays `cut` normally. P's `cut` operates separately on the runs between U+FFFD
characters and preserves every U+FFFD, including template-written characters.
Thus P cannot join literal bytes across an opaque occurrence; B still detects an
opener created by cutting a template-written U+FFFD (for example `<!�-- >` under
`cut:"�"`). No sentinel is reserved in authored text. The choice can conservatively
refuse containers when an authored replacement character is cut from a closer.

When authored lazy containers exist, html5ever parses each complete projection
once, using the token-correlated RcDom live-element predicate used by the existing
final-page **E** check. A container registers only if its own authored start tag
creates a live element in **B, P and E**. Blank detects split openers preserved by
empty page values; placeholder detects split closers assembled by removing values.
Comments, raw text, ignored tags and inert template contents cannot register.
A later authored closer can restore liveness while context is known; the explicit
cutoff for uncertain transforms is permanent. Untracked pages and tracked pages
without authored lazy candidates require neither additional parse. Parsing feeds
non-candidate regions in bulk with token correlation disabled, and candidate
regions at exact delimiter boundaries. The complete HTML still passes through
the tokenizer and tree builder; no context or subtree is skipped. Script and encoding-indicator feed
suspensions are drained before changing correlation offsets.

Cache fragments store both literal-only projections alongside fragment text on the
same cached string object, atomically under Django's usual key. Misses compute
this metadata even during plain/WebSocket rendering. Hits reuse it without
rendering the body. Legacy plain entries, external overwrites and serializers
that strip metadata fail closed for the complete tracked render, including
candidates before the fragment: unknown later literal bytes can remove earlier
elements through HTML tree-building. **Clear fragment caches on upgrade** to avoid
lost lazy registrations until such entries expire. Fragment HTML and cache keys
are unchanged.

Literal-only byte-identical filters keep following-container authority.
Renderer-owned neutral marker bytes do not count as values. These rules concern
rendered content with provenance; ordinary view values and expression operands
have no authored-render sideband, as described below.

Liveness models template text and literal expression output on the rendered branch,
with opaque output replaced as described above. Ordinary attribute interpolation
and value output containing `<`, `>`, quotes or `--` do not by themselves drop
following-container authority. Both projections can conservatively refuse a live
page container: for example an empty opaque value between pieces of an authored
closer leaves that closer split in P. Empty opaque tag results behave the same way.

The two projections do not model all possible non-literal values or earlier HTML
contexts produced by those values. Authors must not hide lazy containers with
values. For example a value emitting `<!--` followed by a value emitting `-->`,
`<{{ tag }}>` with `tag="script"`, or `<scr{{ middle }}>` with `middle="ipt"` can
hide a container until another value closes it; neither B nor P recreates that
value-generated opener. A following authored container can register if it is live
in B, P and E. This is not an all-values tokenizer proof.
An expression such as
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
a value cannot
supply the closer for an inert context opened by literal text or literal-derived
expression output. Only containers live in all three trees (B, P and E) keep
start-tag authority and origin addresses.
Source annotation grants no liveness verdict; inactive branches do not determine
the rendered branch's liveness.

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

M1 split end tags (`</textarea{{ v }}>`, `</script{{ v }}>`) and split comment
closers (`--{{ v }}>`), including byte-identical custom-filter captures (N4),
remain split by U+FFFD in P (and are blank in B). They cannot restore authority
for containers hidden by an authored opener. Value-generated hiding contexts
remain outside the model as described above.
