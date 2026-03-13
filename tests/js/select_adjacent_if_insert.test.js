/**
 * Regression tests for #383: DOM patcher inserts new siblings inside <select>
 * when adjacent {% if %} block expands.
 *
 * When an adjacent {% if %} block becomes true, the InsertChild patch may
 * resolve its parent path to the <select> element rather than the surrounding
 * container, causing new sibling nodes to be inserted inside the <select>.
 * The fix detects the mismatch (non-option child targeting a <select>) and
 * redirects the insert to the correct parent so the new node becomes a sibling.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    url: 'http://localhost',
    runScripts: 'dangerously',
});

if (!dom.window.CSS) dom.window.CSS = {};
if (!dom.window.CSS.escape) {
    dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
}

dom.window.eval(clientCode);

const { _applySinglePatch } = dom.window.djust;
const document = dom.window.document;

describe('InsertChild: non-option into SELECT redirects to parent', () => {
    let container;
    let select;

    beforeEach(() => {
        // Build: <div dj-id="root" dj-live><select dj-id="sel"><option>A</option></select></div>
        container = document.createElement('div');
        container.setAttribute('dj-id', 'root');
        container.setAttribute('dj-live', '');
        select = document.createElement('select');
        select.setAttribute('dj-id', 'sel');
        const opt = document.createElement('option');
        opt.textContent = 'A';
        select.appendChild(opt);
        container.appendChild(select);
        document.body.appendChild(container);
    });

    afterEach(() => {
        document.body.innerHTML = '';
    });

    it('inserts non-option node as sibling of <select>, not inside it', () => {
        _applySinglePatch({
            type: 'InsertChild',
            d: 'sel',          // path resolves to <select>
            path: [],
            index: 0,
            node: { tag: 'div', attrs: { 'dj-id': 'newdiv' }, children: [] },
        });

        // The <div> should be a sibling of <select> inside container, not inside <select>
        const newDiv = document.querySelector('[dj-id="newdiv"]');
        expect(newDiv).not.toBeNull();
        expect(newDiv.parentElement).toBe(container);
        expect(newDiv.parentElement).not.toBe(select);
        // select should still only have its original option
        expect(select.children.length).toBe(1);
    });

    it('does NOT redirect <option> inserts — options still go inside <select>', () => {
        _applySinglePatch({
            type: 'InsertChild',
            d: 'sel',          // path resolves to <select>
            path: [],
            index: 0,
            node: { tag: 'option', attrs: { 'dj-id': 'opt2' }, children: [{ text: 'B' }] },
        });

        const newOpt = select.querySelector('[dj-id="opt2"]');
        expect(newOpt).not.toBeNull();
        expect(newOpt.parentElement).toBe(select);
    });

    it('does NOT redirect <optgroup> inserts — optgroups belong inside <select>', () => {
        _applySinglePatch({
            type: 'InsertChild',
            d: 'sel',
            path: [],
            index: 0,
            node: { tag: 'optgroup', attrs: { 'dj-id': 'og1', label: 'Group' }, children: [] },
        });

        const optgroup = select.querySelector('[dj-id="og1"]');
        expect(optgroup).not.toBeNull();
        expect(optgroup.parentElement).toBe(select);
    });

    it('preserves existing <select> children when redirect fires', () => {
        _applySinglePatch({
            type: 'InsertChild',
            d: 'sel',
            path: [],
            index: 0,
            node: { tag: 'p', attrs: { 'dj-id': 'newp' }, children: [] },
        });

        // Original option still inside select, no extra children added
        expect(select.querySelector('option')).not.toBeNull();
        expect(select.children.length).toBe(1);

        // New <p> is a container child, not inside select
        const newP = document.querySelector('[dj-id="newp"]');
        expect(newP).not.toBeNull();
        expect(newP.parentElement).toBe(container);
    });
});
