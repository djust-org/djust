/**
 * Tests for streaming — real-time partial DOM updates (src/17-streaming.js)
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>
            <div dj-root>
                ${bodyHtml}
            </div>
        </body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;

    // Suppress console
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };

    try {
        window.eval(clientCode);
    } catch (e) {
        // client.js may throw on missing DOM APIs
    }

    return { window, dom, document: dom.window.document };
}

describe('streaming', () => {
    describe('handleStreamMessage', () => {
        it('append op adds HTML to target', () => {
            const { window, document } = createEnv('<div id="messages"></div>');

            window.djust.handleStreamMessage({
                stream: 'chat',
                ops: [{ op: 'append', target: '#messages', html: '<p>Hello</p>' }],
            });

            const el = document.querySelector('#messages');
            expect(el.innerHTML).toContain('<p>Hello</p>');
        });

        it('prepend op inserts before firstChild', () => {
            const { window, document } = createEnv('<div id="list"><p>Second</p></div>');

            window.djust.handleStreamMessage({
                stream: 'feed',
                ops: [{ op: 'prepend', target: '#list', html: '<p>First</p>' }],
            });

            const el = document.querySelector('#list');
            expect(el.firstChild.textContent).toBe('First');
        });

        it('replace op sets innerHTML', () => {
            const { window, document } = createEnv('<div id="content">Old stuff</div>');

            window.djust.handleStreamMessage({
                stream: 'content',
                ops: [{ op: 'replace', target: '#content', html: '<span>New content</span>' }],
            });

            const el = document.querySelector('#content');
            expect(el.innerHTML).toBe('<span>New content</span>');
        });

        it('delete op removes element', () => {
            const { window, document } = createEnv('<div id="msg-42">To be removed</div>');

            window.djust.handleStreamMessage({
                stream: 'messages',
                ops: [{ op: 'delete', target: '#msg-42' }],
            });

            expect(document.querySelector('#msg-42')).toBeNull();
        });

        it('text op with append mode appends text', () => {
            const { window, document } = createEnv('<div id="output">Hello</div>');

            window.djust.handleStreamMessage({
                stream: 'llm',
                ops: [{ op: 'text', target: '#output', text: ' World', mode: 'append' }],
            });

            expect(document.querySelector('#output').textContent).toBe('Hello World');
        });

        it('text op with replace mode replaces text', () => {
            const { window, document } = createEnv('<div id="output">Old</div>');

            window.djust.handleStreamMessage({
                stream: 'llm',
                ops: [{ op: 'text', target: '#output', text: 'New', mode: 'replace' }],
            });

            expect(document.querySelector('#output').textContent).toBe('New');
        });

        it('text op with prepend mode prepends text', () => {
            const { window, document } = createEnv('<div id="output">World</div>');

            window.djust.handleStreamMessage({
                stream: 'llm',
                ops: [{ op: 'text', target: '#output', text: 'Hello ', mode: 'prepend' }],
            });

            expect(document.querySelector('#output').textContent).toBe('Hello World');
        });

        it('error op shows .dj-stream-error div', () => {
            const { window, document } = createEnv('<div id="output">Partial</div>');

            window.djust.handleStreamMessage({
                stream: 'llm',
                ops: [{ op: 'error', target: '#output', error: 'Something failed' }],
            });

            const errorEl = document.querySelector('#output .dj-stream-error');
            expect(errorEl).not.toBeNull();
            expect(errorEl.textContent).toBe('Something failed');
            expect(errorEl.getAttribute('role')).toBe('alert');
        });

        it('start op sets data-stream-active', () => {
            const { window, document } = createEnv('<div id="output"></div>');

            window.djust.handleStreamMessage({
                stream: 'llm',
                ops: [{ op: 'start', target: '#output' }],
            });

            expect(document.querySelector('#output').getAttribute('data-stream-active')).toBe('true');
        });

        it('done op removes data-stream-active and clears from activeStreams', () => {
            const { window, document } = createEnv('<div id="output" data-stream-active="true"></div>');

            // Start the stream first so it's in activeStreams
            window.djust.handleStreamMessage({
                stream: 'llm',
                ops: [{ op: 'start', target: '#output' }],
            });

            const activesBefore = window.djust.getActiveStreams();
            expect(activesBefore['llm']).toBeDefined();

            window.djust.handleStreamMessage({
                stream: 'llm',
                ops: [{ op: 'done', target: '#output' }],
            });

            expect(document.querySelector('#output').getAttribute('data-stream-active')).toBeNull();
            const activesAfter = window.djust.getActiveStreams();
            expect(activesAfter['llm']).toBeUndefined();
        });
    });

    describe('getActiveStreams', () => {
        it('returns an object', () => {
            const { window } = createEnv('<div id="output"></div>');
            const streams = window.djust.getActiveStreams();
            expect(typeof streams).toBe('object');
        });

        it('tracks active streams', () => {
            const { window } = createEnv('<div id="output"></div>');

            window.djust.handleStreamMessage({
                stream: 'my_stream',
                ops: [{ op: 'append', target: '#output', html: '<p>Test</p>' }],
            });

            const streams = window.djust.getActiveStreams();
            expect(streams['my_stream']).toBeDefined();
            expect(streams['my_stream'].started).toBeGreaterThan(0);
        });
    });

    describe('stream:update custom events', () => {
        it('dispatches stream:update event on append', () => {
            const { window, document } = createEnv('<div id="target"></div>');
            const events = [];
            document.querySelector('#target').addEventListener('stream:update', (e) => {
                events.push(e.detail);
            });

            window.djust.handleStreamMessage({
                stream: 'test',
                ops: [{ op: 'append', target: '#target', html: '<p>New</p>' }],
            });

            expect(events.length).toBe(1);
            expect(events[0].op).toBe('append');
            expect(events[0].stream).toBe('test');
        });
    });

    describe('exports', () => {
        it('exposes handleStreamMessage and getActiveStreams', () => {
            const { window } = createEnv();
            expect(typeof window.djust.handleStreamMessage).toBe('function');
            expect(typeof window.djust.getActiveStreams).toBe('function');
        });
    });
});
