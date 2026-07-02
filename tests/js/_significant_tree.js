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

    installCaseSensitiveDjIdResolution(window);

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
 * Make `[dj-id="…"]` selector resolution CASE-SENSITIVE in the jsdom window, so
 * the harness matches real-browser semantics (#1977 follow-up).
 *
 * djust dj-ids are base62 (`0-9a-zA-Z`) from a monotonic counter, so any render
 * past ~38 nodes emits sibling ids that differ only by case (`c` vs `C`). The
 * client resolves structural ops with `node.querySelector(':scope >
 * [dj-id="C"]')` (12-vdom-patch.js). Real browsers match custom-attribute VALUES
 * case-sensitively (`dj-id` is not in the HTML spec's ASCII-case-insensitive
 * attribute set), so `[dj-id="C"]` never matches `dj-id="c"`. jsdom's selector
 * engine (nwsapi) matches them case-INSENSITIVELY, so without this shim the
 * harness resolves the wrong sibling for any fixture that crosses ~38 nodes
 * where a structural op targets a case-colliding sibling — silently
 * false-positing AND (worse) masking real VDOM regressions.
 *
 * The shim wraps `querySelector` on Element/Document/DocumentFragment and only
 * INTERVENES when nwsapi returns a dj-id that case-folds to the requested value
 * but isn't an exact match — then it re-resolves case-sensitively among the
 * native (case-insensitive) candidate set. Every other selector, and every
 * exact match, delegates to native untouched (so escaped/special-char selectors
 * are unaffected). The client only ever uses single `[dj-id="X"]` tokens via
 * `querySelector`, so this is the complete surface.
 */
function installCaseSensitiveDjIdResolution(window) {
    const DJID = /\[dj-id="([^"]*)"\]/;
    const protos = [
        window.Element && window.Element.prototype,
        window.Document && window.Document.prototype,
        window.DocumentFragment && window.DocumentFragment.prototype,
    ].filter(Boolean);

    for (const proto of protos) {
        const nativeQS = proto.querySelector;
        const nativeQSA = proto.querySelectorAll;
        proto.querySelector = function (selector) {
            const result = nativeQS.call(this, selector);
            const m = typeof selector === 'string' && selector.match(DJID);
            if (!result || !m) return result;
            const want = m[1];
            const got = result.getAttribute('dj-id');
            if (got === want) return result; // exact match — nothing to correct
            // Only correct nwsapi's case-insensitivity: same value up to case,
            // but not an exact match. Re-resolve case-sensitively among the
            // native candidate set (a superset of the exact-case matches).
            if (got != null && got.toLowerCase() === want.toLowerCase()) {
                const all = nativeQSA.call(this, selector);
                for (let i = 0; i < all.length; i++) {
                    if (all[i].getAttribute('dj-id') === want) return all[i];
                }
                return null; // no exact-case sibling — the id is genuinely absent
            }
            return result; // difference is not a case issue — leave native as-is
        };
    }
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
