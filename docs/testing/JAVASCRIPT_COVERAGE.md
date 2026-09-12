# JavaScript source coverage

Run from the repository root after `npm ci`:

```bash
npm run test:coverage
# Equivalent Make target:
make test-js-coverage
```

This prepares instrumented scripts, runs the JavaScript suite using
`vitest.coverage.config.js`, writes `coverage/index.html`,
`coverage/coverage-final.json`, and `coverage/coverage-summary.json`, and enforces
regression floors. CI runs this command after the ordinary `npm test` suite and
uploads the `javascript-coverage` artifact, including on failure.

Use the npm command rather than bare `vitest --coverage`: preparing the script
maps and selecting the coverage setup are required. `npm test` continues to run
against uninstrumented production scripts.

## How attribution works

Most browser tests read the shipped bundles with `readFileSync` and execute them
with `eval` or `new Function`. V8 sees anonymous executions and cannot attribute
them to the configured source files. Some source fragments also share an opening
block or class across file boundaries, so they are not independent programs.

The preparation step reconstructs each bundle from its ordered source fragments
and verifies that it exactly matches the committed bundle. A stale bundle fails
with instructions to run `make build-js`. It then instruments the executable
bundle with Istanbul and records a concatenation source map. Coverage reads of
`client.js` and `debug-panel.js` use these instrumented scripts; production files
on disk are never modified. The output maps counters back to the original
`python/djust/static/djust/src/` files, including `src/debug/`.

Each source file starts with its zero-hit counter map, so unexecuted modules stay
in the denominator. Delimiter-only fragments have empty records because a closing
brace has no executable location. JSDOM contexts are collected once after each
test, even when the test has already closed its window.

Tests that execute an independently parseable source module should use:

```javascript
import { readScript } from './coverage-support/instrument.js';
const source = readScript('./python/djust/static/djust/src/07-form-data.js');
```

`readScript` returns original text during normal tests and instrumented code during
coverage. Its identity source map ensures that module and bundle execution merge
into the same source locations without duplicating the denominator. Keep ordinary
`readFileSync` for assertions about source text. Text inspection and raw extracted
snippets do not earn execution coverage. Minified-bundle tests continue to execute
the actual minified artifacts unchanged; they do not contribute source coverage.

## Measured baseline and floors

The initial corrected measurement on September 12, 2026 was:

| Metric | Measured | Enforced floor |
| --- | ---: | ---: |
| Lines | 68.18% | 67% |
| Statements | 65.87% | 65% |
| Functions | 63.70% | 63% |
| Branches | 54.62% | 54% |

The previous 85% settings reported 0% for these dynamically loaded scripts and
were not enforced by the JavaScript CI job. They were not evidence of 85% coverage.
The new floors provide a small margin for platform-dependent branches and protect
the measured baseline. Raise them deliberately as coverage improves; do not lower
them or remove source files to hide regressions. Reaching 85% remains additional
test work, not a result of fixing instrumentation.

The attribution regression tests verify positive hits, a deliberately untaken
branch, identical source locations for bundle/module execution, collection without
double-counting contexts, and rejection of stale bundles. Zero coverage in a
module now means the instrumented execution did not reach it; inspect the HTML
report and its tests before treating that as a production defect.
