/**
 * Harness fidelity: dj-id resolution must be CASE-SENSITIVE (#1977 follow-up).
 *
 * djust dj-ids are base62 (`0-9a-zA-Z`) drawn from a monotonic counter, so any
 * render past ~38 nodes emits sibling ids that differ only by case (e.g. `c`
 * and `C`). The client resolves structural ops by
 * `node.querySelector(':scope > [dj-id="C"]')` (12-vdom-patch.js: RemoveChild /
 * InsertChild ref / MoveChild, and getNodeByPath Strategy-1).
 *
 * Real browsers match custom-attribute VALUES case-sensitively (per the HTML
 * spec's attribute-selector rules — `dj-id` is not in the ASCII-case-insensitive
 * attribute set), so `[dj-id="C"]` never matches `dj-id="c"` in Chrome/Firefox.
 * jsdom's selector engine (nwsapi) matches them case-INSENSITIVELY, so the
 * client-faithful VDOM harness (`vdom_client_faithful_diff.test.js`) silently
 * resolves the WRONG sibling for any fixture that crosses ~38 nodes where a
 * structural op targets a case-colliding sibling — a `RemoveChild{child_d:"C"}`
 * deletes the `c` node instead, which can both false-positive and (worse) MASK a
 * real VDOM regression.
 *
 * `createHarnessDom` therefore installs a case-sensitive `[dj-id="…"]` resolution
 * shim so the harness environment matches real-browser semantics. These tests
 * pin that: without the shim BOTH assertions below fail (jsdom returns the `c`
 * node for `[dj-id="C"]`).
 */

import { describe, it, expect } from 'vitest';
import fs from 'fs';
import { createHarnessDom } from './_significant_tree.js';

const CASE_COLLIDING_HTML =
    '<div id="root">' +
    '<table><tbody dj-id="0">' +
    '<tr dj-id="c"><td>lower</td></tr>' +
    '<tr dj-id="C"><td>upper</td></tr>' +
    '</tbody></table>' +
    '</div>';

describe('harness dj-id resolution is case-sensitive (real-browser fidelity)', () => {
    it('querySelector([dj-id="C"]) resolves the C node, not the case-colliding c node', () => {
        const { window } = createHarnessDom(CASE_COLLIDING_HTML);
        const tbody = window.document.querySelector('[dj-id="0"]');

        expect(
            tbody.querySelector(':scope > [dj-id="C"]')?.textContent,
            'upper-case dj-id="C" must resolve the upper row'
        ).toBe('upper');
        expect(
            tbody.querySelector(':scope > [dj-id="c"]')?.textContent,
            'lower-case dj-id="c" must resolve the lower row'
        ).toBe('lower');
    });

    it('RemoveChild{child_d:"C"} via the real client removes the C row, leaving c', async () => {
        const { window } = createHarnessDom(CASE_COLLIDING_HTML);
        const root = window.document.querySelector('#root');

        await window.djust.applyPatches(
            [{ type: 'RemoveChild', path: [0, 0], d: '0', index: 1, child_d: 'C' }],
            root
        );

        const remaining = Array.from(root.querySelectorAll('tr')).map((tr) => ({
            id: tr.getAttribute('dj-id'),
            text: tr.textContent,
        }));
        expect(remaining, 'only the lower (dj-id="c") row must remain').toEqual([
            { id: 'c', text: 'lower' },
        ]);
    });

    it('client resolves dj-id VALUE selectors only via single-token querySelector (shim surface invariant)', () => {
        // The shim patches `querySelector` and extracts the FIRST `[dj-id="…"]`
        // token, matching the client's actual usage. If the client ever resolves
        // a dj-id VALUE via `querySelectorAll`/`closest`/`matches`, or via a
        // multi-token descendant selector, the shim would silently revert to
        // jsdom's case-insensitive matching with NO failure signal (the concern
        // raised in PR #1978 review). Pin the surface so such a change fails
        // loudly HERE, prompting the shim to be extended.
        const srcDir = './python/djust/static/djust/src';
        const offenders = [];
        for (const file of fs.readdirSync(srcDir)) {
            if (!file.endsWith('.js')) continue;
            const src = fs.readFileSync(`${srcDir}/${file}`, 'utf-8');
            src.split('\n').forEach((line, i) => {
                // dj-id VALUE selector reached through a non-querySelector API...
                if (/\.(querySelectorAll|closest|matches)\([^)]*dj-id="/.test(line)) {
                    offenders.push(`${file}:${i + 1} ${line.trim()}`);
                }
                // ...or two dj-id tokens in one selector (only the first is
                // case-corrected by the shim's regex).
                if (/querySelector\([^)]*dj-id="[^)]*dj-id="/.test(line)) {
                    offenders.push(`${file}:${i + 1} (multi-token) ${line.trim()}`);
                }
            });
        }
        expect(
            offenders,
            'dj-id VALUE selectors must be single-token querySelector-only; ' +
                'extend installCaseSensitiveDjIdResolution if this changes'
        ).toEqual([]);
    });
});
