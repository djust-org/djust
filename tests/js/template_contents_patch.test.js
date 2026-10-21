/**
 * A `<template>`'s children live in `template.content`, which DOM child
 * lists do not show. The server VDOM now carries a template's contents as its
 * children (crates/djust_vdom tests/test_template_contents.rs), and the client
 * must build, address and morph them in `.content`:
 *
 * - a template a patch inserts gets its children in `.content` (it used to
 *   arrive empty: they were appended as DOM children, which `.content` and
 *   `cloneNode`/`importNode` of the content never see);
 * - a patch addressed into a template (SetText, InsertChild, RemoveChild,
 *   MoveChild) resolves through `.content`;
 * - a full re-render morph updates `.content`.
 *
 * The patches are the Rust differ's own output (djust._rust.diff_html).
 */

import { afterEach, describe, expect, it } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

let dom;
afterEach(() => {
    if (dom) dom.window.close();
    dom = null;
});

function setup(rootInner) {
    dom = new JSDOM(
        `<!DOCTYPE html><html><body><div dj-view="app.V" dj-root dj-id="0">${rootInner}</div></body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' });
    dom.window.eval(`
        window.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
        window.DJUST_USE_WEBSOCKET = false;
    `);
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom.window;
}

const contentTexts = (tpl) => Array.from(tpl.content.children).map((c) => c.textContent);

describe('<template> contents over VDOM patches', () => {
    it('an inserted template carries its contents in .content', async () => {
        const win = setup('<p dj-id="1">x</p>');
        const ok = await win.djust.applyPatches([{
            type: 'InsertChild', path: [], d: '0', index: 1,
            node: {
                tag: 'template', attrs: { 'data-t': '', 'dj-id': '2' }, text: null, key: null,
                children: [{
                    tag: 'li', attrs: { class: 'o', 'dj-id': '3' }, text: null, key: null,
                    children: [{ tag: '#text', attrs: {}, children: [], text: 'one', key: null }],
                }],
            },
        }]);
        expect(ok).toBe(true);
        const tpl = win.document.querySelector('template[data-t]');
        expect(tpl.childNodes.length).toBe(0);
        expect(contentTexts(tpl)).toEqual(['one']);
        expect(tpl.content.firstElementChild.className).toBe('o');
        // What a page script does with it.
        const clone = tpl.content.cloneNode(true);
        expect(clone.firstElementChild.textContent).toBe('one');
    });

    it('patches inside an existing template land in .content', async () => {
        const win = setup('<template dj-id="1" data-state="u"><li dj-id="2">a</li></template>');
        const ok = await win.djust.applyPatches([
            { type: 'SetAttr', path: [0], d: '1', key: 'data-state', value: 'r' },
            { type: 'SetText', path: [0, 0, 0], text: 'a!' },
            {
                type: 'InsertChild', path: [0], d: '1', index: 1,
                node: {
                    tag: 'li', attrs: { 'dj-id': '3' }, text: null, key: null,
                    children: [{ tag: '#text', attrs: {}, children: [], text: 'b', key: null }],
                },
            },
        ]);
        expect(ok).toBe(true);
        const tpl = win.document.querySelector('template');
        expect(tpl.getAttribute('data-state')).toBe('r');
        expect(contentTexts(tpl)).toEqual(['a!', 'b']);
        expect(tpl.childNodes.length).toBe(0);
    });

    it('RemoveChild and MoveChild inside a template', async () => {
        const win = setup(
            '<template dj-id="1"><li dj-id="2">a</li><li dj-id="3">b</li><li dj-id="4">c</li></template>');
        let ok = await win.djust.applyPatches([
            { type: 'MoveChild', path: [0], d: '1', from: 2, to: 0, child_d: '4' },
        ]);
        expect(ok).toBe(true);
        const tpl = win.document.querySelector('template');
        expect(contentTexts(tpl)).toEqual(['c', 'a', 'b']);
        ok = await win.djust.applyPatches([
            { type: 'RemoveChild', path: [0], d: '1', index: 1, child_d: '2' },
        ]);
        expect(ok).toBe(true);
        expect(contentTexts(tpl)).toEqual(['c', 'b']);
    });

    it('an index-only RemoveChild (a text node) inside a template', async () => {
        const win = setup('<template dj-id="1">a<b dj-id="2">x</b>c</template>');
        const ok = await win.djust.applyPatches([{ type: 'RemoveChild', path: [0], d: '1', index: 2 }]);
        expect(ok).toBe(true);
        const tpl = win.document.querySelector('template');
        expect(tpl.content.textContent).toBe('ax');
        expect(tpl.content.childNodes.length).toBe(2);
    });

    it('a template element inside a template is built with its own contents', async () => {
        const win = setup('<template dj-id="1"></template>');
        const ok = await win.djust.applyPatches([{
            type: 'InsertChild', path: [0], d: '1', index: 0,
            node: {
                tag: 'template', attrs: { 'dj-id': '2' }, text: null, key: null,
                children: [{
                    tag: 'b', attrs: { 'dj-id': '3' }, text: null, key: null,
                    children: [{ tag: '#text', attrs: {}, children: [], text: 'deep', key: null }],
                }],
            },
        }]);
        expect(ok).toBe(true);
        const inner = win.document.querySelector('template').content.firstElementChild;
        expect(inner.tagName).toBe('TEMPLATE');
        expect(contentTexts(inner)).toEqual(['deep']);
    });

    it('a full re-render morph updates .content and keeps the element', () => {
        const win = setup('<template dj-id="1" data-state="u"><li dj-id="2">a</li></template>');
        const tpl = win.document.querySelector('template');
        const root = win.document.querySelector('[dj-root]');
        const desired = win.document.createElement('div');
        desired.innerHTML = '<template dj-id="1" data-state="r"><li dj-id="2">a</li><li dj-id="3">b</li></template>';
        win.djust.morphChildren(root, desired);
        expect(win.document.querySelector('template')).toBe(tpl);
        expect(tpl.getAttribute('data-state')).toBe('r');
        expect(contentTexts(tpl)).toEqual(['a', 'b']);
    });
});
