/**
 * Tests for enhanced VDOM patch error messages.
 *
 * Verifies that patch failures include patch type, dj-id, and
 * actionable suggestions in DEBUG_MODE.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(opts = {}) {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body>' +
        '<div dj-root dj-liveview-root dj-view="test.View">' +
        '<p id="child0">hello</p>' +
        '</div></body></html>',
        { url: 'http://localhost', runScripts: 'dangerously' }
    );

    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    }

    // Set DEBUG_MODE based on option
    dom.window.DEBUG_MODE = opts.debugMode !== undefined ? opts.debugMode : true;

    // Capture console warnings and errors
    const warnings = [];
    const errors = [];
    const groups = [];
    dom.window.console.warn = (...args) => warnings.push(args.join(' '));
    dom.window.console.error = (...args) => errors.push(args.join(' '));
    dom.window.console.groupCollapsed = (...args) => groups.push(args.join(' '));
    dom.window.console.groupEnd = () => {};
    dom.window.console.log = () => {};

    // Mock WebSocket
    const sentMessages = [];
    dom.window.WebSocket = class MockWebSocket {
        constructor() {
            this.readyState = 1;
            this._sent = sentMessages;
            Promise.resolve().then(() => {
                if (this.onopen) this.onopen({});
            });
        }
        send(msg) { this._sent.push(JSON.parse(msg)); }
        close() {}
    };

    dom.window.eval(clientCode);

    return { dom, warnings, errors, groups };
}

describe('VDOM patch error messages', () => {
    it('includes patch type in failure message when node not found', async () => {
        const { dom, warnings } = createDom();

        // Apply a patch to a non-existent path
        const patches = [
            { type: 'SetText', path: [99], text: 'new text' }
        ];

        const result = await dom.window.djust.applyPatches(patches);

        // Should have failed
        expect(result).toBe(false);

        // Warning should include the patch type
        const patchWarn = warnings.find(w => w.includes('SetText'));
        expect(patchWarn).toBeDefined();
    });

    it('includes dj-id in failure message when provided', async () => {
        const { dom, warnings } = createDom();

        const patches = [
            { type: 'Replace', path: [99], d: 'my-component-id', node: { tag: 'div', attrs: {}, children: [] } }
        ];

        await dom.window.djust.applyPatches(patches);

        const patchWarn = warnings.find(w => w.includes('my-component-id'));
        expect(patchWarn).toBeDefined();
    });

    it('shows debug group with suggested causes in DEBUG_MODE', async () => {
        const { dom, groups } = createDom({ debugMode: true });

        const patches = [
            { type: 'SetAttr', path: [99], key: 'class', value: 'active' }
        ];

        await dom.window.djust.applyPatches(patches);

        const group = groups.find(g => g.includes('Patch detail'));
        expect(group).toBeDefined();
    });

    it('includes failed indices in batch error summary', async () => {
        const { dom, errors } = createDom();

        // Create multiple patches where some fail
        const patches = [
            { type: 'SetText', path: [0, 0], text: 'updated' },  // should succeed (child0 text node)
            { type: 'SetText', path: [99], text: 'fail1' },       // fails
            { type: 'SetText', path: [98], text: 'fail2' },       // fails
        ];

        await dom.window.djust.applyPatches(patches);

        const batchError = errors.find(e => e.includes('patches failed'));
        expect(batchError).toBeDefined();
        expect(batchError).toMatch(/indices:/);
    });

    it('includes parent element info in path traversal failure', async () => {
        const { dom, warnings } = createDom({ debugMode: true });

        // Try to traverse to an index that doesn't exist
        const patches = [
            { type: 'SetText', path: [0, 99], text: 'fail' }
        ];

        await dom.window.djust.applyPatches(patches);

        const pathWarn = warnings.find(w => w.includes('Path traversal failed'));
        expect(pathWarn).toBeDefined();
        // Should include parent element info
        expect(pathWarn).toMatch(/parent:/);
    });

    it('does not throw when SetAttr targets a text node (#622)', async () => {
        const { dom, errors } = createDom();

        // path [0, 0] resolves to the text node inside <p id="child0">
        const patches = [
            { type: 'SetAttr', path: [0, 0], key: 'class', value: 'active' }
        ];

        const result = await dom.window.djust.applyPatches(patches);

        // Should fail gracefully (returned false), not throw
        expect(result).toBe(false);
        // Should NOT have a JS error from setAttribute on text node
        const thrownError = errors.find(e => e.includes('Error applying patch'));
        expect(thrownError).toBeUndefined();
    });

    it('does not throw when RemoveAttr targets a text node (#622)', async () => {
        const { dom, errors } = createDom();

        const patches = [
            { type: 'RemoveAttr', path: [0, 0], key: 'class' }
        ];

        const result = await dom.window.djust.applyPatches(patches);
        expect(result).toBe(false);
        const thrownError = errors.find(e => e.includes('Error applying patch'));
        expect(thrownError).toBeUndefined();
    });

    it('does not throw when InsertChild targets a text node (#622)', async () => {
        const { dom, errors } = createDom();

        const patches = [
            { type: 'InsertChild', path: [0, 0], index: 0, node: { tag: 'span', attrs: {}, children: [] } }
        ];

        const result = await dom.window.djust.applyPatches(patches);
        expect(result).toBe(false);
        const thrownError = errors.find(e => e.includes('Error applying patch'));
        expect(thrownError).toBeUndefined();
    });

    it('does not throw when RemoveChild targets a text node (#622)', async () => {
        const { dom, errors } = createDom();

        const patches = [
            { type: 'RemoveChild', path: [0, 0], index: 0 }
        ];

        const result = await dom.window.djust.applyPatches(patches);
        expect(result).toBe(false);
        const thrownError = errors.find(e => e.includes('Error applying patch'));
        expect(thrownError).toBeUndefined();
    });

    it('does not throw when MoveChild targets a text node (#622)', async () => {
        const { dom, errors } = createDom();

        const patches = [
            { type: 'MoveChild', path: [0, 0], from: 0, to: 1 }
        ];

        const result = await dom.window.djust.applyPatches(patches);
        expect(result).toBe(false);
        const thrownError = errors.find(e => e.includes('Error applying patch'));
        expect(thrownError).toBeUndefined();
    });

    it('skips regular HTML comments in path traversal (only counts dj-if comments)', async () => {
        // Regression test: the Rust VDOM parser drops regular HTML comments
        // but preserves <!--dj-if--> placeholders. The JS patcher must match
        // this behavior when computing child indices, otherwise paths computed
        // by the Rust diff point to wrong nodes.
        const dom = new JSDOM(
            '<!DOCTYPE html><html><body>' +
            '<div dj-root dj-liveview-root dj-view="test.View">' +
            '<!-- Hero Section -->' +
            '<section id="hero">Hero</section>' +
            '<!-- Main Content -->' +
            '<main id="content"><div id="counter">0</div></main>' +
            '</div></body></html>',
            { url: 'http://localhost', runScripts: 'dangerously' }
        );

        if (!dom.window.CSS) dom.window.CSS = {};
        if (!dom.window.CSS.escape) {
            dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
        }
        dom.window.DEBUG_MODE = false;
        dom.window.console.warn = () => {};
        dom.window.console.error = () => {};
        dom.window.console.log = () => {};

        const sentMessages = [];
        dom.window.WebSocket = class MockWebSocket {
            constructor() {
                this.readyState = 1;
                this._sent = sentMessages;
                Promise.resolve().then(() => { if (this.onopen) this.onopen({}); });
            }
            send(msg) { this._sent.push(JSON.parse(msg)); }
            close() {}
        };

        dom.window.eval(clientCode);

        // Rust VDOM sees: [0]=<section>, [1]=<main> (comments dropped)
        // Path [1, 0, 0] should reach the text "0" inside #counter
        const patches = [
            { type: 'SetText', path: [1, 0, 0], text: '1' }
        ];

        const result = await dom.window.djust.applyPatches(patches);
        expect(result).toBe(true);

        const counter = dom.window.document.getElementById('counter');
        expect(counter.textContent).toBe('1');
    });

    it('still counts dj-if placeholder comments in path traversal', async () => {
        const dom = new JSDOM(
            '<!DOCTYPE html><html><body>' +
            '<div dj-root dj-liveview-root dj-view="test.View">' +
            '<!--dj-if-->' +
            '<p id="visible">shown</p>' +
            '</div></body></html>',
            { url: 'http://localhost', runScripts: 'dangerously' }
        );

        if (!dom.window.CSS) dom.window.CSS = {};
        if (!dom.window.CSS.escape) {
            dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
        }
        dom.window.DEBUG_MODE = false;
        dom.window.console.warn = () => {};
        dom.window.console.error = () => {};
        dom.window.console.log = () => {};

        const sentMessages = [];
        dom.window.WebSocket = class MockWebSocket {
            constructor() {
                this.readyState = 1;
                this._sent = sentMessages;
                Promise.resolve().then(() => { if (this.onopen) this.onopen({}); });
            }
            send(msg) { this._sent.push(JSON.parse(msg)); }
            close() {}
        };

        dom.window.eval(clientCode);

        // <!--dj-if--> counts as child [0], <p> is child [1]
        // Path [1, 0] should reach the text "shown" inside <p>
        const patches = [
            { type: 'SetText', path: [1, 0], text: 'updated' }
        ];

        const result = await dom.window.djust.applyPatches(patches);
        expect(result).toBe(true);

        const p = dom.window.document.getElementById('visible');
        expect(p.textContent).toBe('updated');
    });
});
