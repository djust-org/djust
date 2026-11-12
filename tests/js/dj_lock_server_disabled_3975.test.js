/**
 * #3975: the `dj-lock` unlock ran AFTER the response's patches and set
 * `disabled = false` on every locked control, overwriting a control the same
 * response had just rendered `disabled` (same ordering defect as #3963).
 *
 * Real sequence, over the HTTP fallback: click a `dj-lock` button, the client
 * locks it, the response arrives with patches for the same button.
 * Ordering invariant: the response has the last word; what it left alone is
 * unlocked.
 */
import { describe, expect, it } from 'vitest';
import { legacy, setup } from './_form_events_harness.js';

// Significant child paths: root -> [p, button, div].
const PAGE = `
    <p>Ready</p>
    <button dj-click="go" dj-lock>Go</button>
    <div dj-click="go" dj-lock>Panel</div>
`;
const PARAGRAPH = [0];
const BUTTON = [1];
const PANEL = [2];

async function click(selector, patches) {
    const t = setup(PAGE, { go: legacy() }, () => ({ patches }));
    const el = t.$(selector);
    el.dispatchEvent(new t.win.MouseEvent('click', { bubbles: true }));
    // Precondition: the lock is visible before the response.
    expect(el.hasAttribute('data-djust-locked')).toBe(true);
    await t.settle();
    return { t, el };
}

describe('dj-lock does not overwrite what the response rendered (#3975)', () => {
    it('keeps a button the server rendered disabled', async () => {
        const { t, el } = await click('button', [
            { type: 'SetText', path: PARAGRAPH, text: 'Done' },
            { type: 'SetAttr', path: BUTTON, key: 'disabled', value: '' },
        ]);
        expect(t.$('p').textContent).toBe('Done'); // the patches did land
        expect(el.disabled).toBe(true);
        expect(el.hasAttribute('data-djust-locked')).toBe(false);
    });

    it('unlocks a button the response left alone', async () => {
        const { el } = await click('button', [{ type: 'SetText', path: PARAGRAPH, text: 'Done' }]);
        expect(el.disabled).toBe(false);
        expect(el.hasAttribute('data-djust-locked')).toBe(false);
    });

    it('unlocks a button on an empty response', async () => {
        const { el } = await click('button', []);
        expect(el.disabled).toBe(false);
        expect(el.hasAttribute('data-djust-locked')).toBe(false);
    });

    it('keeps the djust-locked class a non-form element the server rendered with it', async () => {
        const { el } = await click('div[dj-click]', [
            { type: 'SetAttr', path: PANEL, key: 'class', value: 'djust-locked' },
        ]);
        expect(el.classList.contains('djust-locked')).toBe(true);
        expect(el.hasAttribute('data-djust-locked')).toBe(false);
    });

    it('removes the lock class from a non-form element the response left alone', async () => {
        const { el } = await click('div[dj-click]', []);
        expect(el.classList.contains('djust-locked')).toBe(false);
        expect(el.hasAttribute('data-djust-locked')).toBe(false);
    });
});
