# JavaScript Testing Guide

This document describes how to run and maintain the JavaScript tests for
djust's client (`python/djust/static/djust/`).

## Overview

- **Runner:** [vitest](https://vitest.dev/) with a JSDOM/happy-dom environment.
- **Where tests live:** `tests/js/*.test.js`.
- **What they exercise:** the built bundle `client.js` (evaluated inside a
  JSDOM window via `tests/js/_helpers.js#createDom`) and, for module-level
  units, the individual source modules under `static/djust/src/`.

There is exactly **one** implementation of the client. Everything that ships
is concatenated from `static/djust/src/[0-9]*.js` by `scripts/build-client.sh`
into `client.js` / `client.min.js`. Files outside `src/` are either build
outputs or documented standalone assets — see `CONTRIBUTING.md` (Pattern 3)
and `tests/js/non-bundle-importers-2659.test.js`, which fails if a test ever
imports a `static/djust/` file that is not shipped.

## Test Structure

```
tests/js/
├── _helpers.js                 # createDom(), nextFrame(), makeMessageEvent() …
├── <feature>.test.js           # one file per src/ feature (required for new modules)
└── <issue-slug>-NNNN.test.js   # regression tests, named after the issue
python/djust/static/djust/
├── src/NN-*.js                 # the ONLY inputs to the bundle
├── client.js                   # build output (readable; what tests load)
└── client.min.js               # build output (what ships)
```

`createDom(bodyHtml, opts)` returns a JSDOM instance with `client.js`
evaluated, a `MockWebSocket` installed and `DOMContentLoaded` fired. Use it
for anything that touches the DOM, event binding, VDOM patching or the
WebSocket client. For a pure function in one module, read the module source
and evaluate it, or drive it through `window.djust.*` on the booted DOM.

## Running Tests

```bash
npm test                                   # all JS tests
npx vitest run tests/js/foo.test.js        # one file
npx vitest                                 # watch mode
npx vitest run --coverage                  # coverage over static/djust/src/
```

Rebuild the bundle after editing `src/` — tests load `client.js`, not the
modules:

```bash
make build-js          # scripts/build-client.sh (also refreshes client-sizes.json)
```

## Writing New Tests

```javascript
import { describe, it, expect } from 'vitest';
import { createDom, nextFrame } from './_helpers.js';

describe('dj-foo', () => {
    it('does the thing', async () => {
        const dom = createDom('<button dj-foo="x">go</button>');
        dom.window.document.querySelector('button').click();
        await nextFrame(dom);
        expect(dom.window.document.body.textContent).toContain('done');
    });
});
```

### Best Practices

- **Every new `src/` feature file needs a test file.** CI checks this.
- **Own the async primitive.** Stub `requestAnimationFrame`, timers and
  observers with something the test drives explicitly; never assert on
  wall-clock timing (see the retro rules in `CLAUDE.md`).
- **Build the DOM the way the browser does.** For morph/patch tests use
  `innerHTML` with the whitespace a real SSR page carries, not
  `createElement`/`appendChild`.
- **Gate-off before you call it done.** Revert the change under test, confirm
  at least one new test fails, restore it.
- **`console.log` only behind `globalThis.djustDebug`** in source; in tests,
  spy on `dom.window.console` rather than the Node console.
- **Structural pins.** When a fix routes N call sites through one helper, pin
  the call-site *set* (a count), so the next unqualified site fails loudly.

## Debugging

```bash
DEBUG=1 npx vitest run tests/js/foo.test.js     # keep console output
npx vitest run --reporter=verbose               # per-test names
```

Set `dom.window.djustDebug = true` before evaluating the client to enable the
client's own debug logging inside the JSDOM window.

## Continuous Integration

`npm test` runs in the `js-tests` job; `npm run lint` (eslint) gates on
errors. Both run in the pre-commit/pre-push hooks locally.

## FAQ

**Q: I edited `static/djust/decorators.js` / `js/pwa.js` — where did they go?**
They were removed in #2659. `decorators.js` was a never-shipped duplicate of
the debounce/throttle/cache/state-bus logic that lives in `src/`; `js/pwa.js`
was loaded by nothing. Edit the `src/` module instead and rebuild.

**Q: Can I test the minified bundle?**
Yes — `tests/js/min_bundle_applypatches_1676.test.js` evaluates
`client.min.js` to catch mangling regressions. Prefer `client.js` for
readability unless the behaviour under test is minification-specific.
