/**
 * Regression tests for issue #1255: Web Components allowlist + the
 * `window.djustAllowedTags` opt-in extension hook.
 *
 * Background: `createNodeFromVNode` (12-vdom-patch.js) had a hardcoded
 * `ALLOWED_HTML_TAGS` whitelist of standard HTML tags. Anything else
 * (custom elements like `<sl-button>`, `<my-component>`, `<model-viewer>`)
 * silently became a `<span>` because the factory pattern only knew the
 * built-in tags. This is wrong for two reasons:
 *
 *   1. Per the HTML spec, custom elements MUST contain a hyphen, so the
 *      hyphen-rule is a reliable, spec-grounded discriminator that
 *      preserves the security argument (`document.createElement` validates
 *      tag-name format and refuses script-injection inputs).
 *   2. Library tags (Shoelace, Lit, Stencil, Lightning, etc.) are
 *      legitimate first-class consumers of djust LiveView — silently
 *      replacing them breaks the framework for any team using a
 *      design-system layer.
 *
 * The fix: tags containing `-` are accepted as Web Components, and an
 * additional `window.djustAllowedTags` Set lets app code register extras
 * (rare; mainly for non-hyphenated proprietary names).
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom() {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body><div dj-root dj-liveview-root></div></body></html>`,
        { url: 'http://localhost', runScripts: 'dangerously' }
    );
    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    }
    dom.window.WebSocket = class MockWebSocket {
        constructor() {
            this.readyState = 1;
            Promise.resolve().then(() => { if (this.onopen) this.onopen({}); });
        }
        send() {}
        close() {}
    };
    // Silence unmocked console output.
    dom.window.console = {
        log: () => {}, warn: () => {}, error: () => {}, debug: () => {},
        info: () => {}, group: () => {}, groupCollapsed: () => {}, groupEnd: () => {}
    };
    try { dom.window.eval(clientCode); } catch (e) { /* client.js may throw on missing APIs */ }
    return dom;
}

describe('VDOM Web Components allowlist (#1255)', () => {
    it('creates a custom-element <sl-button> instead of falling back to <span>', () => {
        const dom = createDom();
        const fn = dom.window.djust.createNodeFromVNode;
        expect(typeof fn).toBe('function');

        const node = fn({ tag: 'sl-button', attrs: { variant: 'primary' }, children: [] });
        expect(node.tagName.toLowerCase()).toBe('sl-button');
        expect(node.tagName.toLowerCase()).not.toBe('span');
        expect(node.getAttribute('variant')).toBe('primary');
    });

    it('creates a multi-hyphen <my-app-component>', () => {
        const dom = createDom();
        const fn = dom.window.djust.createNodeFromVNode;
        const node = fn({ tag: 'my-app-component', attrs: {}, children: [] });
        expect(node.tagName.toLowerCase()).toBe('my-app-component');
    });

    it('creates a popular library component <model-viewer>', () => {
        const dom = createDom();
        const fn = dom.window.djust.createNodeFromVNode;
        const node = fn({ tag: 'model-viewer', attrs: { src: 'a.glb' }, children: [] });
        expect(node.tagName.toLowerCase()).toBe('model-viewer');
        expect(node.getAttribute('src')).toBe('a.glb');
    });

    it('still falls back to <span> for unknown non-hyphenated tags', () => {
        const dom = createDom();
        const fn = dom.window.djust.createNodeFromVNode;
        const node = fn({ tag: 'totallymadeup', attrs: {}, children: [] });
        // No hyphen → not a custom element → safe fallback to span.
        expect(node.tagName.toLowerCase()).toBe('span');
    });

    it('respects the window.djustAllowedTags opt-in extension Set', () => {
        const dom = createDom();
        const fn = dom.window.djust.createNodeFromVNode;
        // App registers a non-hyphenated proprietary tag.
        dom.window.djustAllowedTags = new Set(['mycompany']);
        const node = fn({ tag: 'mycompany', attrs: {}, children: [] });
        expect(node.tagName.toLowerCase()).toBe('mycompany');
    });

    it('preserves built-in tag handling (regression guard)', () => {
        const dom = createDom();
        const fn = dom.window.djust.createNodeFromVNode;
        const div = fn({ tag: 'div', attrs: { class: 'foo' }, children: [] });
        expect(div.tagName.toLowerCase()).toBe('div');
        expect(div.getAttribute('class')).toBe('foo');
    });

    it('preserves SVG tag handling (regression guard)', () => {
        const dom = createDom();
        const fn = dom.window.djust.createNodeFromVNode;
        const path = fn({ tag: 'path', attrs: { d: 'M0 0L1 1' }, children: [] });
        expect(path.tagName.toLowerCase()).toBe('path');
        // SVG context is determined by parent — single-element fn call goes
        // through the SVG factory branch but the namespace is SVG_NAMESPACE.
        expect(path.namespaceURI).toBe('http://www.w3.org/2000/svg');
    });
});
