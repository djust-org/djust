/**
 * Tests for input value clearing when name attribute changes.
 *
 * When the VDOM patcher morphs an input into a different field
 * (e.g., wizard step 1 name → step 2 email), the old value
 * should not leak into the new field.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(html) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${html}</body></html>`,
        { url: 'http://localhost', runScripts: 'dangerously' }
    );
    dom.window.eval(clientCode);
    return dom;
}

describe('morphElement input name change', () => {
    it('clears input value when name attribute changes', () => {
        const dom = createDom(
            '<div dj-root dj-view="test"><form>' +
            '<input name="username" type="text" value="" dj-id="1" />' +
            '</form></div>'
        );
        const doc = dom.window.document;
        const input = doc.querySelector('input');

        // Simulate user typing
        input.value = 'TypedValue';

        // Create desired element with different name
        const desired = doc.createElement('input');
        desired.setAttribute('name', 'email');
        desired.setAttribute('type', 'email');
        desired.setAttribute('value', '');
        desired.setAttribute('dj-id', '1');

        // Morph
        dom.window.djust.morphElement(input, desired);

        // Value should be cleared (not 'TypedValue')
        expect(input.value).toBe('');
        expect(input.name).toBe('email');
        expect(input.type).toBe('email');
    });

    it('preserves input value when name stays the same', () => {
        const dom = createDom(
            '<div dj-root dj-view="test"><form>' +
            '<input name="search" type="text" value="" dj-id="1" />' +
            '</form></div>'
        );
        const doc = dom.window.document;
        const input = doc.querySelector('input');
        input.value = 'UserQuery';
        input.focus();

        // Desired element has same name but different placeholder
        const desired = doc.createElement('input');
        desired.setAttribute('name', 'search');
        desired.setAttribute('type', 'text');
        desired.setAttribute('value', '');
        desired.setAttribute('placeholder', 'New placeholder');
        desired.setAttribute('dj-id', '1');

        dom.window.djust.morphElement(input, desired);

        // Value should be preserved (user is still typing)
        expect(input.value).toBe('UserQuery');
        expect(input.name).toBe('search');
    });

    it('clears value when morphing select with name change', () => {
        const dom = createDom(
            '<div dj-root dj-view="test"><form>' +
            '<select name="country" dj-id="1"><option value="us">US</option></select>' +
            '</form></div>'
        );
        const doc = dom.window.document;
        const select = doc.querySelector('select');
        select.value = 'us';

        // Desired select with different name
        const desired = doc.createElement('select');
        desired.setAttribute('name', 'state');
        desired.setAttribute('dj-id', '1');
        const opt = doc.createElement('option');
        opt.value = 'ca';
        opt.textContent = 'CA';
        desired.appendChild(opt);

        dom.window.djust.morphElement(select, desired);

        expect(select.name).toBe('state');
    });
});
