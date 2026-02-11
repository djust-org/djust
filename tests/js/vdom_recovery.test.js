/**
 * Regression tests for VDOM patch failure recovery (#259).
 *
 * When applyPatches() fails (e.g., {% if %} blocks shifting DOM structure),
 * the client sends a request_html message and the server responds with
 * html_recovery for DOM morphing — instead of reloading the page.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom() {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body>' +
        '<div data-djust-root data-liveview-root data-djust-view="test.View">' +
        '<p id="content">original</p>' +
        '</div></body></html>',
        { url: 'http://localhost', runScripts: 'dangerously' }
    );

    // Polyfill CSS.escape
    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    }

    // Track all WebSocket messages sent
    const sentMessages = [];

    // Mock WebSocket that fires onopen synchronously after handlers are set
    dom.window.WebSocket = class MockWebSocket {
        constructor() {
            this.readyState = 1; // OPEN
            this._sent = sentMessages;
            // Schedule onopen for next microtick so event handlers are attached first
            Promise.resolve().then(() => {
                if (this.onopen) this.onopen({});
            });
        }
        send(data) {
            this._sent.push(JSON.parse(data));
        }
        close() {}
    };
    dom.window.WebSocket.OPEN = 1;
    dom.window.WebSocket.CONNECTING = 0;
    dom.window.WebSocket.CLOSING = 2;
    dom.window.WebSocket.CLOSED = 3;

    // Suppress console noise
    const origConsole = dom.window.console;
    dom.window.console = {
        log: () => {},
        error: origConsole.error.bind(origConsole),
        warn: () => {},
        debug: () => {},
        info: () => {},
    };

    dom.window.eval(clientCode);

    return { dom, sentMessages };
}

describe('VDOM patch failure recovery', () => {
    describe('html_recovery message handling', () => {
        it('morphs DOM content when html_recovery is received', async () => {
            const { dom } = createDom();
            // Wait for onopen to fire
            await new Promise(r => setTimeout(r, 10));

            const ws = dom.window.djust.liveViewInstance;
            expect(ws).toBeDefined();

            const root = dom.window.document.querySelector('[data-djust-root]');
            expect(root.querySelector('#content').textContent).toBe('original');

            ws.handleMessage({
                type: 'html_recovery',
                html: '<div data-djust-root><p id="content">recovered</p></div>',
                version: 5,
            });

            expect(root.querySelector('#content').textContent).toBe('recovered');
        });

        it('preserves existing DOM nodes during morph (element reuse)', async () => {
            const { dom } = createDom();
            await new Promise(r => setTimeout(r, 10));

            const ws = dom.window.djust.liveViewInstance;
            const root = dom.window.document.querySelector('[data-djust-root]');
            const originalP = root.querySelector('#content');

            ws.handleMessage({
                type: 'html_recovery',
                html: '<div data-djust-root><p id="content">updated</p></div>',
                version: 2,
            });

            // morphChildren reuses elements with matching id
            const updatedP = root.querySelector('#content');
            expect(updatedP).toBe(originalP);
            expect(updatedP.textContent).toBe('updated');
        });

        it('handles recovery HTML with new elements', async () => {
            const { dom } = createDom();
            await new Promise(r => setTimeout(r, 10));

            const ws = dom.window.djust.liveViewInstance;
            const root = dom.window.document.querySelector('[data-djust-root]');

            ws.handleMessage({
                type: 'html_recovery',
                html: '<div data-djust-root><p id="content">kept</p><span id="new">added</span></div>',
                version: 3,
            });

            expect(root.querySelector('#content').textContent).toBe('kept');
            expect(root.querySelector('#new').textContent).toBe('added');
        });
    });

    describe('request_html sent on patch failure', () => {
        it('sends request_html when patches fail to apply', async () => {
            const { dom, sentMessages } = createDom();
            await new Promise(r => setTimeout(r, 10));

            const ws = dom.window.djust.liveViewInstance;
            expect(ws).toBeDefined();

            // Simulate receiving a mount response to initialize version
            ws.handleMessage({
                type: 'html_recovery',
                html: '<div data-djust-root><p>init</p></div>',
                version: 1,
            });

            sentMessages.length = 0; // Clear init messages

            // Send patches that will fail (path points to non-existent node)
            ws.lastEventName = 'test_event';
            ws.lastTriggerElement = null;
            ws.handleMessage({
                type: 'patch',
                patches: [
                    { type: 'SetText', path: [999, 999], text: 'nonexistent' }
                ],
                version: 2,
            });

            const requestHtml = sentMessages.find(m => m.type === 'request_html');
            expect(requestHtml).toBeDefined();
        });
    });

    describe('full recovery flow', () => {
        it('recovers DOM after patch failure followed by html_recovery', async () => {
            const { dom, sentMessages } = createDom();
            await new Promise(r => setTimeout(r, 10));

            const ws = dom.window.djust.liveViewInstance;
            const root = dom.window.document.querySelector('[data-djust-root]');

            // 1. Initialize with html_recovery
            ws.handleMessage({
                type: 'html_recovery',
                html: '<div data-djust-root><p id="content">v1</p></div>',
                version: 1,
            });
            expect(root.querySelector('#content').textContent).toBe('v1');

            sentMessages.length = 0;

            // 2. Receive bad patches — triggers request_html
            ws.lastEventName = 'toggle';
            ws.handleMessage({
                type: 'patch',
                patches: [{ type: 'SetText', path: [999, 999], text: 'bad' }],
                version: 2,
            });

            expect(sentMessages.find(m => m.type === 'request_html')).toBeDefined();

            // 3. Server responds with html_recovery
            ws.handleMessage({
                type: 'html_recovery',
                html: '<div data-djust-root><p id="content">v2-recovered</p></div>',
                version: 2,
            });

            expect(root.querySelector('#content').textContent).toBe('v2-recovered');
        });
    });
});
