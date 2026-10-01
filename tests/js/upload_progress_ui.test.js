/**
 * Tests for the upload progress UI (#3289, src/15-uploads.js).
 *
 * `[dj-upload-progress="<slot>"]` containers used to be inert: the ref is
 * minted client-side on file pick, so a template could never render
 * `data-upload-ref`. The client now binds an existing bar (e.g.
 * {% theme_progress %}) or renders a <progress> per file when the container
 * is empty.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import { randomBytes } from 'node:crypto';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(bodyHtml = '') {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body>'
        + `<div dj-view="test.V" dj-root>${bodyHtml}</div></body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;
    class MockWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor() {
            this.readyState = MockWebSocket.OPEN;
            this.bufferedAmount = 0;
        }
        send() {}
        close() {}
    }
    window.WebSocket = MockWebSocket;
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };
    if (!window.crypto || !window.crypto.getRandomValues) {
        Object.defineProperty(window, 'crypto', {
            value: { getRandomValues: (arr) => { arr.set(randomBytes(arr.length)); return arr; } },
            configurable: true,
        });
    }
    try {
        window.eval(clientCode);
    } catch (e) {
        // client.js may throw on missing DOM APIs
    }
    try {
        window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
    } catch (_) { /* noop */ }

    // `liveViewWS` is bundle-scoped; the instance the bundle created is the
    // one uploadFile() talks to, so record on its prototype.
    const ws = window.djust.liveViewInstance;
    if (!ws) throw new Error('harness: liveViewInstance missing — bundle did not init');
    window.__sent = [];
    Object.getPrototypeOf(ws).sendMessage = function (m) { window.__sent.push(m); };
    return { window, document: window.document };
}

function startUpload(window, slot, name = 'a.txt') {
    const file = new window.File(['hello'], name, { type: 'text/plain' });
    const p = window.djust.uploads.uploadFile(file, slot, {});
    p.catch(() => {});
    // The ref is the one registered with the server.
    const reg = window.__sent.filter((m) => m.type === 'upload_register').pop();
    return { ref: reg.ref, promise: p };
}

describe('upload progress UI (#3289)', () => {
    describe('render mode — empty container', () => {
        it('renders a progress item with the ref when an upload starts', () => {
            const { window, document } = createEnv('<div dj-upload-progress="docs"></div>');
            const { ref } = startUpload(window, 'docs');

            const item = document.querySelector('[dj-upload-progress="docs"] [data-upload-ref]');
            expect(item).not.toBeNull();
            expect(item.getAttribute('data-upload-ref')).toBe(ref);
            expect(item.querySelector('progress.upload-progress-bar')).not.toBeNull();
            expect(item.textContent).toContain('a.txt');
        });

        it('drives the rendered bar from server progress messages', () => {
            const { window, document } = createEnv('<div dj-upload-progress="docs"></div>');
            const { ref } = startUpload(window, 'docs');

            window.djust.uploads.handleProgress({ ref, progress: 40, status: 'uploading' });
            const bar = document.querySelector('[dj-upload-progress="docs"] progress');
            expect(bar.value).toBe(40);
            expect(document.querySelector('.upload-progress-text').textContent).toBe('40%');

            window.djust.uploads.handleProgress({ ref, progress: 100, status: 'complete' });
            expect(bar.value).toBe(100);
            expect(document.querySelector('[data-upload-ref]').getAttribute('data-upload-status'))
                .toBe('complete');
        });

        it('renders one item per file and ignores other slots', () => {
            const { window, document } = createEnv(
                '<div dj-upload-progress="docs"></div><div dj-upload-progress="other"></div>'
            );
            startUpload(window, 'docs', 'one.txt');
            startUpload(window, 'docs', 'two.txt');

            expect(document.querySelectorAll('[dj-upload-progress="docs"] .upload-progress-item').length).toBe(2);
            expect(document.querySelector('[dj-upload-progress="other"]').children.length).toBe(0);
        });

        it('re-attaches when a DOM morph wiped the item mid-upload', () => {
            const { window, document } = createEnv('<div dj-upload-progress="docs"></div>');
            const { ref } = startUpload(window, 'docs');
            document.querySelector('[dj-upload-progress="docs"]').innerHTML = '';

            window.djust.uploads.handleProgress({ ref, progress: 60, status: 'uploading' });
            expect(document.querySelector('[dj-upload-progress="docs"] progress').value).toBe(60);
        });
    });

    describe('bind mode — container already holds a bar', () => {
        const themed =
            '<div class="progress-wrapper" dj-upload-progress="docs">' +
            '<div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">' +
            '<div class="progress-bar" style="width: 0%"></div></div></div>';

        it('binds the themed bar instead of rendering another', () => {
            const { window, document } = createEnv(themed);
            const { ref } = startUpload(window, 'docs');

            const host = document.querySelector('[dj-upload-progress="docs"]');
            expect(host.getAttribute('data-upload-ref')).toBe(ref);
            expect(host.querySelectorAll('[data-upload-generated]').length).toBe(0);
            expect(host.querySelectorAll('[role="progressbar"]').length).toBe(1);
        });

        it('updates the track aria value and the fill width', () => {
            const { window, document } = createEnv(themed);
            const { ref } = startUpload(window, 'docs');

            window.djust.uploads.handleProgress({ ref, progress: 55, status: 'uploading' });
            expect(document.querySelector('[role="progressbar"]').getAttribute('aria-valuenow')).toBe('55');
            expect(document.querySelector('.progress-bar').style.width).toBe('55%');
        });

        it('re-binds the same bar for the next file and resets it', () => {
            const { window, document } = createEnv(themed);
            const first = startUpload(window, 'docs', 'one.txt');
            window.djust.uploads.handleProgress({ ref: first.ref, progress: 100, status: 'complete' });
            const second = startUpload(window, 'docs', 'two.txt');

            expect(second.ref).not.toBe(first.ref);
            const host = document.querySelector('[dj-upload-progress="docs"]');
            expect(host.getAttribute('data-upload-ref')).toBe(second.ref);
            expect(document.querySelector('.progress-bar').style.width).toBe('0%');
        });
    });

    describe('bare <progress> container', () => {
        it('drives the element itself instead of appending a div inside it', () => {
            const { window, document } = createEnv('<progress dj-upload-progress="docs" max="100" value="0"></progress>');
            const { ref } = startUpload(window, 'docs');

            const bar = document.querySelector('progress[dj-upload-progress="docs"]');
            expect(bar.getAttribute('data-upload-ref')).toBe(ref);
            expect(bar.children.length).toBe(0);

            window.djust.uploads.handleProgress({ ref, progress: 70, status: 'uploading' });
            expect(bar.value).toBe(70);
        });
    });

    describe('indeterminate theme_progress', () => {
        const indeterminate =
            '<div class="dj-progress-wrapper" dj-upload-progress="docs">' +
            '<div class="dj-progress dj-progress-indeterminate" role="progressbar" ' +
            'aria-valuemin="0" aria-valuemax="100"></div></div>';

        it('stays indeterminate until a real percentage arrives', () => {
            const { window, document } = createEnv(indeterminate);
            const { ref } = startUpload(window, 'docs');
            const track = document.querySelector('[role="progressbar"]');
            expect(track.classList.contains('dj-progress-indeterminate')).toBe(true);
            expect(track.hasAttribute('aria-valuenow')).toBe(false);

            window.djust.uploads.handleProgress({ ref, progress: 30, status: 'uploading' });
            expect(track.classList.contains('dj-progress-indeterminate')).toBe(false);
            expect(track.getAttribute('aria-valuenow')).toBe('30');
            const fill = track.querySelector('.dj-progress-bar');
            expect(fill).not.toBeNull();
            expect(fill.style.width).toBe('30%');
        });
    });

    describe('selector escaping', () => {
        it('does not throw on a slot name or ref containing quotes', () => {
            const { window } = createEnv('<div dj-upload-progress="docs"></div>');
            const { ref } = startUpload(window, 'docs');
            expect(() => window.djust.uploads.handleProgress({ ref: 'x"] , [y="', progress: 1, status: 'uploading' }))
                .not.toThrow();
            const file = new window.File(['a'], 'a.txt', { type: 'text/plain' });
            const p = window.djust.uploads.uploadFile(file, 'we"ird\\slot', {});
            p.catch(() => {});
            // the real slot is untouched and still updates
            window.djust.uploads.handleProgress({ ref, progress: 5, status: 'uploading' });
            expect(window.document.querySelector('[dj-upload-progress="docs"] progress').value).toBe(5);
        });
    });

    describe('existing contract', () => {
        it('still updates .upload-progress-bar under a hand-written data-upload-ref', () => {
            const { window, document } = createEnv(
                '<div data-upload-ref="r1"><div class="upload-progress-bar" style="width: 0%"></div>' +
                '<span class="upload-progress-text">0%</span></div>'
            );
            window.djust.uploads.handleProgress({ ref: 'r1', progress: 25, status: 'uploading' });
            expect(document.querySelector('.upload-progress-bar').style.width).toBe('25%');
            expect(document.querySelector('.upload-progress-text').textContent).toBe('25%');
        });
    });
});
