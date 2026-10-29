# Django partial-root integration follow-up

Status: **not ready for the 2.0 remote branch**. The partial-root candidate is
committed locally, but its complete integration gates have not passed. The
preceding partial and exception fixes were pushed as `9073fc881`.

## Evidence

The first full Django 6.1.2 run of the candidate reported 49,520 passed,
151 failed, 638 skipped, and 3 xfailed. Its relative `PYTHONPATH` and missing
`maturin` in the temporary environment caused some subprocess failures; after
fixing that environment, 94 relevant subprocess and packaging-command tests
passed. The full suite must be repeated with absolute paths on the final commit.

Focused tests exposed genuine compatibility problems:

- Ordinary inherited pages with opaque custom-tag output can lose root
  provenance and fail to render. A fail-closed improvement fixes the malformed
  ordinary-root cases, but 31 catalogue HTTP and WebSocket tests remain red.
- Rendering the complete page for a WebSocket can execute a child mount from
  the shell before the selected live root is known. Discarding it afterwards
  cannot undo application side effects.
- Explicit child reconciliation uses a lexical `div` root scanner. A child in
  a `<main dj-root>` can be classified as a shell child and survive removal;
  HTML5 foster parenting also makes lexical ancestry differ from browser DOM
  ancestry. Existing reconciliation does not carry the specific server-rendered
  child occurrence through postprocessing, so an HTML identifier alone is not
  sufficient proof of ownership.

Two independent fixes are committed locally: `6264e001b` restores the existing
five-field snapshot layout when no partial metadata is present and updates
snapshot diagnostics; `3295a37e5` restores the transient request sidecar and
fixes a test worker-connection leak. The 79-test snapshot/state selection
passed on Django 6.0 and 6.1; the sidecar and related provider selection passed
74 tests on Django 6.1. The extended snapshot format remains incompatible with
older workers, including for some ordinary include/inheritance views that enter
conservative partial-root mode; see the deployment guide.

## Performance

On CPython 3.12.9 and Django 6.1.2, a synthetic update benchmark with 8 warmups
and 40 samples measured median baseline times of 0.46 ms for 100 rows and
4.53–4.74 ms for 1,000 rows. The candidate measured 1.68–1.77 ms and
16.27–16.59 ms respectively. These figures cover include/inheritance fixtures,
not all applications; the roughly 3.5–3.9x regression needs a safe design fix.

## Required work before push

1. Preserve ordinary-page compatibility without allowing uncertain opaque tag
   output to grant root or model-binding authority. A generic fallback and
   blanket opaque-output projection were rejected by regression tests.
2. Preserve root-only WebSocket execution semantics, including shell-child
   side effects. An immutable, render-scoped dependency graph would need to be
   shared across extraction, HTTP, WebSocket, model binding and deferred blocks,
   with changes and dynamic or uninspectable dependencies handled explicitly.
3. Carry render-bound child occurrence identity through the tag bridge and
   postprocessing, then classify surviving occurrences against the same HTML5
   root used by the diff path. Cover non-`div` roots, malformed trees, inert
   content, duplicate or lost metadata, nested children and reconnects.
4. Rebuild, run the focused compatibility and transport tests, commit the final
   tree with hooks, run the full Django 6.1 suite with an absolute `PYTHONPATH`,
   and run `make test-integration` on that exact commit. Push only after these
   gates pass and review the measured performance impact.
