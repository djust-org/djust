/**
 * #4118 — the browser hears why an upload stopped, and uploadFile() settles.
 *
 * Drives the real client (client.js) in JSDOM against a mock socket:
 *  - the view's cancel_upload() arrives as the `djust:upload_progress` push
 *    event: the pending uploadFile() promise rejects with the server's reason
 *    and no more chunks (nor a complete frame) are sent;
 *  - an `upload_progress` with status `cancelled` (a timeout) settles it too;
 *  - an `error` carries the server's message (an UploadError refusal) into the
 *    rejection and into the `djust:upload:progress` detail.
 */

import { describe, it, expect } from 'vitest';
import { PROTOCOL } from './_helpers.js';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const FRAME_CHUNK = 0x01;
const FRAME_COMPLETE = 0x02;

function createPage() {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body><div id="page" dj-view="app.Page" dj-root>' +
        '<p>page</p></div></body></html>',
        { url: 'http://localhost/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const win = dom.window;
    if (!win.CSS) win.CSS = {};
    if (!win.CSS.escape) win.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    const sockets = [];
    win.WebSocket = class MockWebSocket {
        constructor(url) {
            this.url = url;
            this.readyState = 1;
            this.sent = [];
            this.binary = [];
            // Large, so the client paces its chunks with timers and a cancel
            // can land between two of them.
            this.bufferedAmount = 1 << 30;
            sockets.push(this);
            Promise.resolve().then(() => this.onopen && this.onopen({}));
        }
        send(data) {
            if (typeof data === 'string') this.sent.push(JSON.parse(data));
            else this.binary.push(Array.from(new win.Uint8Array(data)));
        }
        close() { this.readyState = 3; }
    };
    win.WebSocket.CONNECTING = 0;
    win.WebSocket.OPEN = 1;
    win.WebSocket.CLOSING = 2;
    win.WebSocket.CLOSED = 3;
    win.console = { log() {}, error() {}, warn() {}, debug() {}, info() {} };
    win.eval(clientCode);
    return { win, sockets };
}

const tick = (ms = 15) => new Promise((resolve) => setTimeout(resolve, ms));

async function startUpload() {
    const page = createPage();
    await tick();
    const socket = page.sockets[0];
    socket.onmessage({ data: JSON.stringify({ type: 'connect', protocol: PROTOCOL, session_id: 's1' }) });
    await tick();
    socket.onmessage({ data: JSON.stringify({
        type: 'mount', view: 'app.Page', version: 1, html: '<p dj-id="1">page</p>', has_ids: true,
    }) });
    await tick();
    page.win.djust.uploads.setConfigs({ doc: { chunk_size: 4, max_file_size: 1000 } });
    const file = new page.win.File(['0123456789abcdef0123'], 'a.txt', { type: 'text/plain' });
    const promise = page.win.djust.uploads.uploadFile(file, 'doc', {});
    const outcome = promise.then(
        (value) => ({ value }),
        (error) => ({ error: error.message }),
    );
    await tick(25);
    const ref = socket.sent.find((f) => f.type === 'upload_register').ref;
    const serve = (frame) => socket.onmessage({ data: JSON.stringify(frame) });
    return { ...page, socket, ref, outcome, serve };
}

const chunksSent = (socket) => socket.binary.filter((b) => b[0] === FRAME_CHUNK).length;
const completeSent = (socket) => socket.binary.some((b) => b[0] === FRAME_COMPLETE);

describe('#4118: the server stopping an upload settles uploadFile()', () => {
    it('rejects on the view\'s cancel (push event) and stops sending chunks', async () => {
        const { win, socket, ref, outcome, serve } = await startUpload();
        const before = chunksSent(socket);
        expect(before).toBeGreaterThan(0);
        expect(before).toBeLessThan(5);
        const events = [];
        win.addEventListener('djust:upload:progress', (e) => events.push(e.detail));

        serve({ type: 'render', effects: [{
            kind: 'push_event',
            event: 'djust:upload_progress',
            payload: { type: 'upload_progress', ref, progress: 0, status: 'cancelled',
                error: 'Upload cancelled by the server.' },
        }] });

        expect(await outcome).toEqual({ error: 'Upload cancelled by the server.' });
        expect(win.djust.uploads.activeUploads.has(ref)).toBe(false);
        expect(events.map((d) => [d.status, d.error])).toEqual([
            ['cancelled', 'Upload cancelled by the server.'],
        ]);
        await tick(120);
        expect(chunksSent(socket)).toBe(before);
        expect(completeSent(socket)).toBe(false);
    });

    it('rejects on a cancelled upload_progress (timeout) with its reason', async () => {
        const { ref, outcome, serve } = await startUpload();
        serve({ type: 'upload_progress', ref, progress: 0, status: 'cancelled',
            error: 'Upload timed out after 30 seconds.' });
        expect(await outcome).toEqual({ error: 'Upload timed out after 30 seconds.' });
    });

    it('a cancelled frame with no reason still settles', async () => {
        const { ref, outcome, serve } = await startUpload();
        serve({ type: 'upload_progress', ref, progress: 0, status: 'cancelled' });
        expect(await outcome).toEqual({ error: 'Upload cancelled' });
    });

    it('an error carries the server\'s refusal message', async () => {
        const { win, ref, outcome, serve } = await startUpload();
        const events = [];
        win.addEventListener('djust:upload:progress', (e) => events.push(e.detail));
        serve({ type: 'upload_progress', ref, progress: 0, status: 'error',
            error: 'Images must be under 2 MB.' });
        expect(await outcome).toEqual({ error: 'Images must be under 2 MB.' });
        expect(events[0].error).toBe('Images must be under 2 MB.');
    });

    it('an error without a message keeps the generic text', async () => {
        const { ref, outcome, serve } = await startUpload();
        serve({ type: 'upload_progress', ref, progress: 0, status: 'error' });
        expect(await outcome).toEqual({ error: 'Upload failed on server' });
    });

    it('ignores other push events', async () => {
        const { win, ref, serve } = await startUpload();
        serve({ type: 'render', effects: [{ kind: 'push_event', event: 'something_else',
            payload: { ref, status: 'cancelled' } }] });
        await tick();
        expect(win.djust.uploads.activeUploads.has(ref)).toBe(true);
    });
});
