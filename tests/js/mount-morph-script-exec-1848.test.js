/**
 * Regression test for #1848 — inline classic <script> inside the dj-root
 * must EXECUTE after the WS-mount morph (#1610), not be silently dropped.
 *
 * Symptom (reporter, browser-verified): an inline <script> placed inside the
 * dj-root (the morphdom-managed region) registering a delegated listener
 * (e.g. document.addEventListener('click', ...)) silently never registers
 * after djust mounts. No console error. Tab switching + code-copy buttons
 * went dead after the 1.0.7 upgrade (worked on 1.0.5rc3).
 *
 * Root cause: on mount, djust HTTP-pre-renders, then MORPHS the pre-rendered
 * DOM against the WS-mount HTML (#1610: morphChildren). morphdom does NOT
 * execute <script> it inserts/re-creates — that is standard browser behavior
 * for DOM nodes that are cloned / inserted rather than HTML-parsed. The
 * non-prerendered branch (`container.innerHTML = data.html`) has the same gap:
 * innerHTML never runs inserted <script> either. Both mount paths re-create the
 * inline <script> but never run it, so its addEventListener is never called.
 *
 * Fix (#1646 structural cure — one helper, both parallel mount paths): a
 * `window.djust._runInsertedScripts(container)` helper re-creates each classic
 * <script> in the morphed subtree via document.createElement('script') +
 * copy attributes + textContent + replaceWith — the only DOM operation that
 * makes the browser execute an already-in-tree inert script. It is called once
 * after BOTH mount branches in 03-websocket.js. Idempotent: a one-time data
 * marker prevents re-running a script on reconnect / re-mount.
 *
 * Reproduction fidelity (CLAUDE.md #1650/#1638/#1724): the DOM is built via
 * innerHTML WITH realistic inter-element whitespace text nodes (what a real
 * SSR/morph page carries), and real <script> elements are used — NOT eval()
 * (eval scopes const/let to the eval call and does not reproduce the bug).
 * The helper is the exact code the mount handler invokes, so this exercises
 * the real path, not a convenient proxy.
 *
 * This test loads the REAL helper from the built client bundle.
 */

import { describe, it, expect, beforeEach } from 'vitest';
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

/**
 * Build a dj-root subtree the way the #1610 mount morph produces it: a temp
 * <div> filled via innerHTML (so inert <script> nodes + inter-element
 * whitespace text nodes are present), whose children are moved into the live
 * dj-root container. After this, the inline <script> is in the live tree but
 * has NOT executed (standard browser semantics) — exactly the bug state.
 */
function buildMorphedDjRoot(scriptText) {
    const root = document.createElement('div');
    root.setAttribute('dj-root', '');
    document.body.appendChild(root);

    const temp = document.createElement('div');
    // innerHTML keeps the inline <script> inert AND preserves whitespace text
    // nodes (real SSR/morph shape — #1650/#1724 fidelity).
    temp.innerHTML =
        '<div class="content">\n' +
        '  <button class="tab-button">Tab</button>\n' +
        '  <script>' + scriptText + '</script>\n' +
        '</div>\n';
    // Move children into the live container (what clone+insert morph does).
    while (temp.firstChild) root.appendChild(temp.firstChild);
    return root;
}

describe('#1848 — inline <script> inside dj-root executes after mount morph', () => {
    beforeEach(() => {
        // Clean slate between cases.
        document.body.innerHTML = '';
        delete window.__djust1848ran;
        delete window.__djust1848runs;
    });

    it('exports the _runInsertedScripts helper on window.djust', () => {
        // The fix MUST expose a callable helper — the mount handler invokes it
        // and this test drives it directly.
        expect(typeof window.djust._runInsertedScripts).toBe('function');
    });

    it('executes an inline classic <script> re-created by the morph', () => {
        const root = buildMorphedDjRoot('window.__djust1848ran = true;');
        // Precondition: the morph left the script inert (this is the bug).
        expect(window.__djust1848ran).toBeUndefined();

        // The fix: run inserted scripts in the morphed container.
        window.djust._runInsertedScripts(root);

        expect(window.__djust1848ran).toBe(true);
    });

    it('registers a delegated document listener so morph-managed clicks fire', () => {
        // This is the reporter's actual scenario: an inline script registering
        // a delegated document click listener for .tab-button.
        const root = buildMorphedDjRoot(
            "document.addEventListener('click', function (e) {" +
            "  if (e.target && e.target.classList.contains('tab-button')) {" +
            "    window.__djust1848ran = true;" +
            "  }" +
            "});"
        );
        // Without running the inserted script, the listener is never registered.
        root.querySelector('.tab-button').dispatchEvent(
            new window.MouseEvent('click', { bubbles: true })
        );
        expect(window.__djust1848ran).toBeUndefined();

        // After the fix the listener registers; the next click fires it.
        window.djust._runInsertedScripts(root);
        root.querySelector('.tab-button').dispatchEvent(
            new window.MouseEvent('click', { bubbles: true })
        );
        expect(window.__djust1848ran).toBe(true);
    });

    it('is idempotent — a re-run (reconnect / re-mount) does not double-execute', () => {
        window.__djust1848runs = 0;
        const root = buildMorphedDjRoot('window.__djust1848runs = (window.__djust1848runs || 0) + 1;');

        window.djust._runInsertedScripts(root);
        expect(window.__djust1848runs).toBe(1);

        // A second call (e.g. WS reconnect re-mount on the same DOM) must NOT
        // run the already-executed script again.
        window.djust._runInsertedScripts(root);
        expect(window.__djust1848runs).toBe(1);
    });

    it('skips type="djust/hook" colocated definition scripts (not classic JS)', () => {
        // Colocated hook definitions (<script type="djust/hook">) are extracted
        // by the hook system, not executed as classic JS. The helper must only
        // re-execute classic scripts (no type, or a JS MIME type).
        const root = document.createElement('div');
        root.setAttribute('dj-root', '');
        document.body.appendChild(root);
        const temp = document.createElement('div');
        temp.innerHTML =
            '<script type="djust/hook">window.__djust1848ran = true;</script>\n';
        while (temp.firstChild) root.appendChild(temp.firstChild);

        window.djust._runInsertedScripts(root);
        // The djust/hook script must NOT have been executed as classic JS.
        expect(window.__djust1848ran).toBeUndefined();
    });

    it('runs an external classic <script src> by re-creating the element', () => {
        // External scripts can't execute in JSDOM (no network), but the helper
        // must still re-create them so the browser fetches+runs them. We assert
        // the inert node was replaced by a FRESH script element carrying src.
        const root = document.createElement('div');
        root.setAttribute('dj-root', '');
        document.body.appendChild(root);
        const temp = document.createElement('div');
        temp.innerHTML = '<script src="/static/page.js"></script>\n';
        const original = temp.querySelector('script');
        while (temp.firstChild) root.appendChild(temp.firstChild);

        window.djust._runInsertedScripts(root);
        const live = root.querySelector('script[src="/static/page.js"]');
        expect(live).toBeTruthy();
        // The re-created element is a DIFFERENT node than the inert original.
        expect(live).not.toBe(original);
        expect(live.getAttribute('src')).toBe('/static/page.js');
    });
});
