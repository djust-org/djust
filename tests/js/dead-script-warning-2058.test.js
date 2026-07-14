/**
 * Regression tests for #2058 — DEBUG-mode-only loud detection of the #1848
 * trap: a classic <script> inserted by a DOM morph/patch/innerHTML operation
 * is NOT executed by the browser ("reload works, navigation doesn't").
 *
 * #1848 itself shipped a *fix* for the two WS-mount paths
 * (`_runInsertedScripts()` in 03-websocket.js — see
 * mount-morph-script-exec-1848.test.js). This issue is different: it's a
 * *detector*, not a fix, for every OTHER insertion path the #1848 fix didn't
 * (couldn't, scope-wise) touch — `InsertSubtree` patches (12-vdom-patch.js),
 * `mount_batch` (03-websocket.js), `html_recovery` (03-websocket.js),
 * `handleEmbeddedUpdate` (03-websocket.js), the SSE mount path (03b-sse.js),
 * streaming ops (17-streaming.js), the instant-shell swap
 * (33-sw-registration.js), layout swaps (40-dj-layout.js), and lazy-render
 * fills (50-lazy-fill.js).
 *
 * The shared helper is `window.djust._warnDeadScripts(root)`: DEBUG-mode-only,
 * scans `root` for `<script>` elements that (a) are "classic" (no type, or a
 * JS MIME type), (b) do NOT carry the `data-djust-script-ran` marker
 * `_runInsertedScripts()` stamps on scripts it already re-executed, and
 * emits one `console.error('[djust] ... (%s). ...', label)` per dead script.
 *
 * Reproduction fidelity (CLAUDE.md #1650/#1638/#1724): case (a) below drives
 * the REAL `InsertSubtree` patch dispatcher (`window.djust._applyInsertSubtree`,
 * the same function 12-vdom-patch.js's `applySinglePatch` calls for a
 * server-emitted `{type: 'InsertSubtree', html: '...'}` patch) rather than a
 * standalone call to the helper — this exercises the actual call site added
 * by #2058, not just the helper in isolation.
 *
 * Gate-off (#1468): manually verified by commenting out the
 * `_warnDeadScripts(fragment)` call in `applyInsertSubtree()`
 * (12-vdom-patch.js), rebuilding via `bash scripts/build-client.sh`, and
 * re-running this file — case (a) went RED (0 console.error calls instead of
 * 1), confirming the test is behavior-meaningful and not tautological. The
 * call site was restored afterward and the bundle rebuilt again before this
 * file was finalized.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    runScripts: 'dangerously',
    url: 'http://localhost/',
});

if (!dom.window.CSS) dom.window.CSS = {};
if (!dom.window.CSS.escape) {
    dom.window.CSS.escape = function (value) {
        return String(value).replace(/([^\w-])/g, '\\$1');
    };
}

dom.window.eval(clientCode);

const { window } = dom;
const document = window.document;

let errorSpy;

beforeEach(() => {
    document.body.innerHTML = '';
    window.DEBUG_MODE = false;
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
    errorSpy.mockRestore();
});

describe('#2058 — window.djust._warnDeadScripts export', () => {
    it('is exported as a callable helper', () => {
        expect(typeof window.djust._warnDeadScripts).toBe('function');
    });
});

describe('#2058 (a) — InsertSubtree patch with a classic inline <script> warns once (DEBUG_MODE=true)', () => {
    it('drives the real applyInsertSubtree call site and warns exactly once, naming the script', () => {
        window.DEBUG_MODE = true;

        const parent = document.createElement('div');
        parent.id = 'parent-2058a';
        parent.setAttribute('dj-id', 'parent-2058a-id');
        document.body.appendChild(parent);

        const html =
            '<!--dj-if id="if-2058a-0"-->' +
            '<script>window.__dj2058aRan = true;</script>' +
            '<!--/dj-if-->';

        const result = window.djust._applyInsertSubtree({
            type: 'InsertSubtree',
            id: 'if-2058a-0',
            html,
            path: [],
            d: 'parent-2058a-id',
            index: 0,
        });
        expect(result).toBe(true);

        // The script is in the DOM (template-parsed) but never executed —
        // the underlying #1848 trap this issue detects.
        expect(parent.querySelector('script')).not.toBeNull();
        expect(window.__dj2058aRan).toBeUndefined();

        // Exactly one loud warning, naming the dead script.
        expect(errorSpy).toHaveBeenCalledTimes(1);
        const [, label] = errorSpy.mock.calls[0];
        expect(label).toContain('__dj2058aRan');
    });
});

describe('#2058 (b) — no warning when DEBUG_MODE is not set', () => {
    it('the same dead-script InsertSubtree produces zero console.error calls', () => {
        // window.DEBUG_MODE left at the beforeEach default (false).
        const parent = document.createElement('div');
        parent.id = 'parent-2058b';
        parent.setAttribute('dj-id', 'parent-2058b-id');
        document.body.appendChild(parent);

        const html =
            '<!--dj-if id="if-2058b-0"-->' +
            '<script>window.__dj2058bRan = true;</script>' +
            '<!--/dj-if-->';

        window.djust._applyInsertSubtree({
            type: 'InsertSubtree',
            id: 'if-2058b-0',
            html,
            path: [],
            d: 'parent-2058b-id',
            index: 0,
        });

        expect(parent.querySelector('script')).not.toBeNull();
        expect(errorSpy).not.toHaveBeenCalled();
    });
});

describe('#2058 (c) — no warning for type="djust/hook" colocated-hook scripts', () => {
    it('skips a djust/hook payload script even in DEBUG_MODE', () => {
        window.DEBUG_MODE = true;

        const root = document.createElement('div');
        root.innerHTML = '<script type="djust/hook" data-hook="Chart">hook.mounted = function () {};</script>';
        document.body.appendChild(root);

        window.djust._warnDeadScripts(root);

        expect(errorSpy).not.toHaveBeenCalled();
    });
});

describe('#2058 (d) — no warning for a subtree without scripts', () => {
    it('a plain subtree with no <script> elements produces zero warnings', () => {
        window.DEBUG_MODE = true;

        const root = document.createElement('div');
        root.innerHTML = '<p>hello</p><button class="tab">Tab</button>';
        document.body.appendChild(root);

        window.djust._warnDeadScripts(root);

        expect(errorSpy).not.toHaveBeenCalled();
    });
});

describe('#2058 — scripts already re-executed are excluded (data-djust-script-ran marker)', () => {
    it('does not warn for a script _runInsertedScripts() already handled', () => {
        window.DEBUG_MODE = true;

        const root = document.createElement('div');
        const temp = document.createElement('div');
        temp.innerHTML = '<div class="content">\n  <script>window.__dj2058ranMarker = true;</script>\n</div>\n';
        while (temp.firstChild) root.appendChild(temp.firstChild);
        document.body.appendChild(root);

        // Simulate the real mount-path sequence: run the #1848 fix first...
        window.djust._runInsertedScripts(root);
        expect(window.__dj2058ranMarker).toBe(true);

        // ...then the #2058 defense-in-depth warning must find nothing dead.
        window.djust._warnDeadScripts(root);
        expect(errorSpy).not.toHaveBeenCalled();
    });
});

describe('#2058 — external <script src> warns with the src as the label', () => {
    it('names the src attribute, not the (empty) inline body', () => {
        window.DEBUG_MODE = true;

        const root = document.createElement('div');
        const temp = document.createElement('div');
        temp.innerHTML = '<script src="/static/page.js"></script>';
        while (temp.firstChild) root.appendChild(temp.firstChild);
        document.body.appendChild(root);

        window.djust._warnDeadScripts(root);

        expect(errorSpy).toHaveBeenCalledTimes(1);
        const [, label] = errorSpy.mock.calls[0];
        expect(label).toBe('/static/page.js');
    });
});

describe('#2058 — application/json data scripts are excluded', () => {
    it('does not warn for a non-classic data script type', () => {
        window.DEBUG_MODE = true;

        const root = document.createElement('div');
        root.innerHTML = '<script type="application/json">{"a":1}</script>';
        document.body.appendChild(root);

        window.djust._warnDeadScripts(root);

        expect(errorSpy).not.toHaveBeenCalled();
    });
});

describe('#2058 — helper no-ops entirely outside DEBUG_MODE regardless of root shape', () => {
    it('a null/undefined root does not throw and does not warn', () => {
        window.DEBUG_MODE = true;
        expect(() => window.djust._warnDeadScripts(null)).not.toThrow();
        expect(() => window.djust._warnDeadScripts(undefined)).not.toThrow();
        expect(errorSpy).not.toHaveBeenCalled();
    });
});
