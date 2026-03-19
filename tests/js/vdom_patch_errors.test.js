/**
 * Tests for enhanced VDOM patch error messages.
 *
 * Verifies that patch failures include patch type, dj-id, and
 * actionable suggestions in DEBUG_MODE.
 */

import { describe, it, expect, beforeEach } from 'vitest';
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
    it('includes patch type in failure message when node not found', () => {
        const { dom, warnings } = createDom();

        // Apply a patch to a non-existent path
        const patches = [
            { type: 'SetText', path: [99], text: 'new text' }
        ];

        const root = dom.window.document.querySelector('[dj-root]');
        const result = dom.window.applyPatches(patches);

        // Should have failed
        expect(result).toBe(false);

        // Warning should include the patch type
        const patchWarn = warnings.find(w => w.includes('SetText'));
        expect(patchWarn).toBeDefined();
    });

    it('includes dj-id in failure message when provided', () => {
        const { dom, warnings } = createDom();

        const patches = [
            { type: 'Replace', path: [99], d: 'my-component-id', node: { tag: 'div', attrs: {}, children: [] } }
        ];

        dom.window.applyPatches(patches);

        const patchWarn = warnings.find(w => w.includes('my-component-id'));
        expect(patchWarn).toBeDefined();
    });

    it('shows debug group with suggested causes in DEBUG_MODE', () => {
        const { dom, groups } = createDom({ debugMode: true });

        const patches = [
            { type: 'SetAttr', path: [99], key: 'class', value: 'active' }
        ];

        dom.window.applyPatches(patches);

        const group = groups.find(g => g.includes('Patch detail'));
        expect(group).toBeDefined();
    });

    it('includes failed indices in batch error summary', () => {
        const { dom, errors } = createDom();

        // Create multiple patches where some fail
        const patches = [
            { type: 'SetText', path: [0, 0], text: 'updated' },  // should succeed (child0 text node)
            { type: 'SetText', path: [99], text: 'fail1' },       // fails
            { type: 'SetText', path: [98], text: 'fail2' },       // fails
        ];

        dom.window.applyPatches(patches);

        const batchError = errors.find(e => e.includes('patches failed'));
        expect(batchError).toBeDefined();
        expect(batchError).toMatch(/indices:/);
    });

    it('includes parent element info in path traversal failure', () => {
        const { dom, warnings } = createDom({ debugMode: true });

        // Try to traverse to an index that doesn't exist
        const patches = [
            { type: 'SetText', path: [0, 99], text: 'fail' }
        ];

        dom.window.applyPatches(patches);

        const pathWarn = warnings.find(w => w.includes('Path traversal failed'));
        expect(pathWarn).toBeDefined();
        // Should include parent element info
        expect(pathWarn).toMatch(/parent:/);
    });
});
