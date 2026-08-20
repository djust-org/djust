/**
 * djustInit() must not run before the rest of the bundle registers its APIs (#2185).
 *
 * The bundle is ONE concatenated IIFE, and `14-init.js` is module 14 of 55.
 * Its tail used to be:
 *
 *     if (document.readyState === 'loading') {
 *         document.addEventListener('DOMContentLoaded', djustInit);
 *     } else {
 *         djustInit();          // <-- synchronous, mid-bundle
 *     }
 *
 * On the `else` branch djustInit() runs while modules 15..55 are still
 * unexecuted, so every `window.djust.*` they publish is still undefined.
 * djustInit() reaches three of them through existence guards:
 *
 *     if (window.djust.extractColocatedHooks) ...   -> 32-colocated-hooks.js
 *     if (window.djust.initVirtualLists) ...        -> 29-virtual-list.js
 *     if (window.djust.initInfiniteScroll) ...      -> 30-infinite-scroll.js
 *
 * The guards make the skip SILENT — no error, no warning. dj-virtual never
 * virtualizes, infinite-scroll observers never attach, colocated hooks never
 * register.
 *
 * It looked intermittent because it depends on `document.readyState` at that
 * instant: a cold parse during HTML streaming is 'loading' (safe branch),
 * while a warm/cached load is already 'interactive' and takes the broken one.
 * Measured in a browser on a failing load: readyState "interactive",
 * typeof window.djust.initVirtualLists === "undefined", and
 * initVirtualLists was never called at all.
 */

import { describe, it, expect } from 'vitest';
import fs from 'fs';
import path from 'path';

const SRC = path.resolve('python/djust/static/djust/src');
const INIT = fs.readFileSync(path.join(SRC, '14-init.js'), 'utf-8');

/**
 * `INIT` with comments removed.
 *
 * A negative assertion run against raw source matches prose as readily as
 * code — this file's first version failed because 14-init.js's own comment
 * says "NOT `Promise.resolve().then(djustInit)`", and the test read that as
 * the banned call. djust canon calls this the `code_only` hole; it is why a
 * source-grep pin was withdrawn in #2167.
 */
const INIT_CODE = INIT
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1');

/** Modules whose `window.djust.*` API djustInit() calls through a guard. */
const LATE_APIS = [
    { fn: 'extractColocatedHooks', module: '32-colocated-hooks.js' },
    { fn: 'initVirtualLists', module: '29-virtual-list.js' },
    { fn: 'initInfiniteScroll', module: '30-infinite-scroll.js' },
];

const moduleIndex = (name) => parseInt(name.match(/^(\d+)/)[1], 10);

describe('#2185 — djustInit must not run mid-bundle', () => {
    it('does not call djustInit() synchronously on the non-loading branch', () => {
        // The whole bug in one assertion: a bare `djustInit();` as the else
        // branch runs before later modules have registered anything.
        const bareSyncCall = /}\s*else\s*{\s*djustInit\(\);\s*}/;
        expect(INIT_CODE).not.toMatch(bareSyncCall);
    });

    it('defers djustInit so the rest of the bundle executes first', () => {
        // A microtask is the smallest correct delay: the remainder of the
        // bundle is synchronous, so it is guaranteed to finish before any
        // microtask drains. setTimeout would also work but costs a task.
        // queueMicrotask specifically: `Promise.resolve().then(djustInit)`
        // would defer correctly but convert an init throw into a silent
        // unhandled rejection, disarming the #1370 TDZ guard.
        expect(INIT_CODE).toMatch(/queueMicrotask\(djustInit\)/);
        expect(INIT_CODE).not.toMatch(/Promise\.resolve\(\)\.then\(djustInit\)/);
    });

    it('still handles the loading branch via DOMContentLoaded', () => {
        expect(INIT).toMatch(/document\.addEventListener\('DOMContentLoaded', djustInit\)/);
    });

    it.each(LATE_APIS)(
        'guards $fn, which module $module registers AFTER 14-init.js',
        ({ fn, module }) => {
            // Precondition: djustInit really does call it through a guard...
            expect(INIT).toContain(`window.djust.${fn}`);

            // ...and the module that publishes it really does sort after
            // 14-init.js, so it is genuinely unexecuted at that point.
            expect(moduleIndex(module)).toBeGreaterThan(moduleIndex('14-init.js'));

            const owner = fs.readFileSync(path.join(SRC, module), 'utf-8');
            expect(owner).toMatch(new RegExp(`djust\\.${fn}\\s*=`));
        }
    );

    it('records why a guarded call is not enough', () => {
        // Documentation-as-test: the comment must survive, because the guard
        // looks defensive and reads as safe. Both bundle lints
        // (check-bundle-init-order.mjs, check-cross-iife-refs.mjs) report
        // CLEAN on the broken form for exactly that reason — they look for
        // bare references, and `if (window.djust.X)` is not one.
        expect(INIT).toMatch(/#2185/);
    });
});
