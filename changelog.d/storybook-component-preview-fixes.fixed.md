- **Fix six component previews on the storybook, five of which were examples
  written against an API the component does not have.** `Meter` renders
  `segments` against a `total` and was being passed `value`/`min`/`max` — all
  three landed in `**kwargs` and the preview showed an empty bar under a label.
  `Rating` was passed `max` (the parameter is `max_stars`) plus a `name` it does
  not take. The `Dropdown` example was `[{}]`, so the preview was a button
  reading "Menu" with nothing to open — and the `Dropdown`/`Modal` examples were
  reduced to one each, because a storybook page declares one descriptor per
  component and renders every example against that same state, so a left/right
  pair shared a single `is_open` and a three-size modal set would have opened
  all three at once. `Modal` additionally had no trigger at all — a dialog's
  trigger belongs to the page that opens it, so the storybook now supplies one.
- **Fix the `CodeSnippet` copy button doing nothing.** It rendered as a
  working control — class, `aria-label`, `type="button"` — wired to no handler.
  It now uses the framework's own `dj-copy="#<id>"`, which copies the named
  element's `textContent` client-side, and the code block carries the matching
  id. Client-side deliberately: a clipboard write is a browser API, so a
  round-trip would add latency and a failure mode to a purely local action.
- **Fix the `Spinner` rendering as the words "Loading..." with no spinner.**
  Two causes. `.dj-spinner` had no CSS anywhere in the repo — not in the
  components stylesheet, not in theming — despite the component documenting six
  `--dj-spinner-*` custom properties and three other call sites emitting the
  class; the rules now exist, including a `prefers-reduced-motion` guard that
  slows the animation rather than stopping it (a frozen spinner reads as a hung
  page). And the screen-reader label used `dj-sr-only`, which matches no rule —
  the utility is `.sr-only`, unprefixed, and every sibling component already
  used that spelling, so the label was not hidden.
- **Rating's `set_rating` no longer errors.** The component's stars dispatch
  `set_rating` and the storybook had no handler, so clicking one produced a
  server error instead of a rating. `StorybookDetailView` now keeps the value.
  It is held as view state rather than as a new descriptor: `Rating` is a value
  input, DEP-002 covers containers, and inventing public framework API to make
  a demo page work is the wrong order of operations.
- **Cache-bust the theming static assets.** `theme_head.html` linked
  `components.js` / `components.css` / `theme.js` with no version, and Django's
  static server sends no `Cache-Control` — so browsers applied heuristic
  freshness (a fraction of the file's age) and kept the copy they already had.
  An edit to any of them was invisible on the page it was made for, which reads
  as "the fix didn't work"; it cost a full debugging cycle here, where a stale
  `components.js` was still calling `stopPropagation()` on dropdown triggers and
  silently eating every `dj-click` djust had bound. Each tag now carries
  `?v=<newest asset mtime>`, so the token moves on an edit rather than on a
  release. Production's hashed filenames already handled this; a dev server
  serving the source did not.
