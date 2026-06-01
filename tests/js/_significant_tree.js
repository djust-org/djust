/**
 * Shared helpers for the client-faithful differential VDOM harness.
 *
 * `createHarnessDom` — spin up a jsdom window with the REAL built client.js
 * evaluated into it, capturing console warn/error so patch failures surface.
 *
 * `normalizeSignificantTree` — canonicalize a DOM subtree into a plain
 * structure for deep-equality, recursing ONLY over the bundle's own
 * `window.djust.getSignificantChildren` so the harness uses the EXACT same
 * significance predicate the patcher uses for index resolution (no second
 * copy to drift — the #1640/#1655 lesson). It captures precisely the things
 * #1678/#1408 corrupt: dj-if marker identity + position in the sibling
 * sequence, element tag + dj-key, and significant text — while ignoring
 * whitespace, non-dj-if comments, and counter-dependent dj-id VALUES (dj-id
 * presence is asserted via `hasDjId` shape only).
 */

import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync(
    './python/djust/static/djust/client.js',
    'utf-8'
);

export function createHarnessDom(initialHtml) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${initialHtml}</body></html>`,
        { runScripts: 'dangerously' }
    );
    const { window } = dom;

    if (!window.CSS) window.CSS = {};
    if (!window.CSS.escape) {
        window.CSS.escape = (s) => String(s).replace(/([^\w-])/g, '\\$1');
    }

    const logs = [];
    window.console = {
        log: (...args) => logs.push(['log', args.join(' ')]),
        error: (...args) => logs.push(['error', args.join(' ')]),
        warn: (...args) => logs.push(['warn', args.join(' ')]),
        debug: () => {},
        info: () => {},
    };

    window.eval(clientCode);
    return { dom, window, logs };
}

/**
 * Return the canonical significant-child array of `node`. Each entry:
 *   element        → { tag, key?, hasDjId, children:[...] }
 *   dj-if marker   → { djif: <open-marker id | 'open' | 'close'> }
 *   significant txt→ { text: <trimmed> }
 */
export function normalizeSignificantTree(node, window) {
    const getSig = window.djust.getSignificantChildren;

    function norm(n) {
        // Element
        if (n.nodeType === 1) {
            const out = { tag: n.tagName.toLowerCase() };
            const key = n.getAttribute('dj-key');
            if (key !== null) out.key = key;
            out.hasDjId = n.hasAttribute('dj-id');
            out.children = getSig(n).map(norm);
            return out;
        }
        // Comment — only dj-if family reaches here (getSignificantChildren filters)
        if (n.nodeType === 8) {
            const t = (n.textContent || '').trim();
            if (/^\/dj-if/.test(t)) return { djif: 'close' };
            const m = t.match(/^dj-if(?:\s+id="([^"]*)")?/);
            return { djif: m && m[1] ? m[1] : 'open' };
        }
        // Significant text
        return { text: (n.textContent || '').trim() };
    }

    return getSig(node).map(norm);
}
