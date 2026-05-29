/**
 * Regression: client.js must be safe to execute a SECOND time in the same
 * document without a parse-time SyntaxError (#1635).
 *
 * `live_redirect` keeps the document alive and morphdom can re-attach the
 * `<script src="client.js">` tag; bfcache `pageshow` re-init follows the same
 * path. Two classic <script> executions share ONE global lexical environment,
 * so any top-level `const`/`let`/`class` that isn't function-scoped is
 * re-declared at compile time on the second execution:
 *
 *   Uncaught SyntaxError: Identifier '_TEXT_INPUT_TYPES' has already been declared
 *
 * The runtime `window._djustClientLoaded` guard can't help — the SyntaxError
 * fires at PARSE time, before the guard is consulted. Wrapping the whole bundle
 * in an IIFE makes every top-level declaration function-scoped, so a second
 * execution creates a fresh scope and never collides.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function runTwice() {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
        runScripts: 'dangerously',
        url: 'http://localhost',
    });
    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) dom.window.CSS.escape = (v) => String(v);

    const errors = [];
    dom.window.addEventListener('error', (e) => {
        errors.push(e.error ? `${e.error.name}: ${e.error.message}` : String(e.message));
    });

    const inject = () => {
        const s = dom.window.document.createElement('script');
        s.textContent = clientCode; // classic inline script — real <script> semantics
        dom.window.document.body.appendChild(s);
    };
    inject();
    inject();
    return { dom, errors };
}

describe('client.js re-evaluation idempotence (#1635)', () => {
    it('does not throw a re-declaration SyntaxError on a second <script> execution', () => {
        const { errors } = runTwice();
        const redeclare = errors.filter(
            (e) => /SyntaxError/.test(e) && /already been declared/.test(e)
        );
        expect(redeclare).toEqual([]);
    });

    it('keeps the runtime double-load guard working (init not run twice)', () => {
        const { dom } = runTwice();
        // The guard sets this once; a second execution must short-circuit, not
        // re-run init. The flag is the observable contract.
        expect(dom.window._djustClientLoaded).toBe(true);
        // And the public namespace is still exposed after both executions.
        expect(dom.window.djust).toBeTruthy();
    });
});
