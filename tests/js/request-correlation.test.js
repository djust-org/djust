/** Overlapping same-trigger requests require distinct, transport-neutral replies. */
import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');

function setup(name) {
    const dom = new JSDOM('<!doctype html><html><body></body></html>', {
        url: 'http://localhost/', runScripts: 'dangerously',
    });
    const { window } = dom;
    window.CSS ??= {};
    window.CSS.escape ??= value => String(value).replace(/([^\w-])/g, '\\$1');
    window.eval(client);
    window.document.body.innerHTML = '<div data-djust-embedded="child"><button id="save" dj-click="save" dj-loading.disable>Save</button></div>';
    const sent = [];
    const transport = new window.djust[name]();
    transport.viewMounted = true;
    if (name === 'LiveViewWebSocket') {
        transport.ws = {readyState: window.WebSocket.OPEN, send: text => sent.push(JSON.parse(text))};
    } else {
        transport.sseBaseUrl = '/djust/sse/test/';
        window.fetch = vi.fn((_url, options) => {
            sent.push(JSON.parse(options.body));
            return Promise.resolve({ok: true, json: async () => ({})});
        });
    }
    const button = window.document.getElementById('save');
    const loading = window.djust.globalLoadingManager;
    loading.scanAndRegister();
    function send() {
        loading.startLoading('save', button);
        return transport.sendEvent('save', {view_id: 'child'}, button);
    }
    function reply(index) {
        return transport.handleMessage({
            type: 'embedded_update', ref: sent[index].ref, view_id: 'child', event_name: 'save',
            html: '<button id="save" dj-click="save" dj-loading.disable>Saved</button>',
        });
    }
    return {dom, transport, sent, button, loading, send, reply};
}

describe.each(['LiveViewWebSocket', 'LiveViewSSE'])('%s request correlation', name => {
    it('assigns a distinct reference to each request', () => {
        const {dom, sent, send} = setup(name);
        try {
            send();
            send();
            expect(Number.isSafeInteger(sent[0].ref)).toBe(true);
            expect(Number.isSafeInteger(sent[1].ref)).toBe(true);
            expect(sent[1].ref).toBeGreaterThan(sent[0].ref);
        } finally { dom.window.close(); }
    });

    it('retains the second request after the first reply and its duplicate', async () => {
        const {dom, transport, button, loading, send, reply} = setup(name);
        try {
            send();
            send();
            await reply(0);
            expect(button.disabled).toBe(true);
            expect(loading.pendingEvents.has('save')).toBe(true);
            expect(transport.lastTriggerElement).toBe(button);
            await reply(0);
            expect(button.disabled).toBe(true);
            expect(transport.lastTriggerElement).toBe(button);
            await reply(1);
            expect(button.disabled).toBe(false);
            expect(loading.pendingEvents.has('save')).toBe(false);
            expect(transport.lastTriggerElement).toBeNull();
        } finally { dom.window.close(); }
    });

    it('awaits the server reply rather than only accepting the outgoing POST', async () => {
        const {dom, send, reply} = setup(name);
        try {
            const response = send();
            expect(typeof response?.then).toBe('function');
            let completed = false;
            response.then(() => { completed = true; });
            await Promise.resolve();
            expect(completed).toBe(false);
            await reply(0);
            await response;
            expect(completed).toBe(true);
        } finally { dom.window.close(); }
    });

    it.each(['noop', 'patch', 'html_update'])('correlates out-of-order %s acknowledgements', async type => {
        const {dom, transport, button, sent, send, loading} = setup(name);
        try {
            const first = send();
            const second = send();
            const frame = index => ({type, ref: sent[index].ref, patches: [], source: 'event'});
            await transport.handleMessage(frame(1));
            await second;
            expect(button.disabled).toBe(true);
            expect(transport.lastTriggerElement).toBe(button);
            await transport.handleMessage(frame(1));
            expect(button.disabled).toBe(true);
            await transport.handleMessage(frame(0));
            await first;
            expect(button.disabled).toBe(false);
            expect(loading.pendingEvents.size).toBe(0);
        } finally { dom.window.close(); }
    });

    it('does not accept unknown refs or background frames as foreground replies', async () => {
        const {dom, transport, button, sent, send} = setup(name);
        try {
            let done = false;
            send().then(() => { done = true; });
            await transport.handleMessage({type: 'noop', ref: sent[0].ref + 100});
            await transport.handleMessage({type: 'noop', ref: sent[0].ref, source: 'async'});
            expect(done).toBe(false);
            expect(button.disabled).toBe(true);
            expect(transport.lastTriggerElement).toBe(button);
        } finally { dom.window.close(); }
    });

    it('a background patch cannot clear an outstanding page-scoped request', async () => {
        const {dom, transport, button, sent, send, loading} = setup(name);
        try {
            button.parentElement.removeAttribute('data-djust-embedded');
            send();
            await transport.handleMessage({type: 'patch', source: 'async', event_name: 'save', patches: []});
            expect(button.disabled).toBe(true);
            expect(loading.pendingEvents.has('save')).toBe(true);
            await transport.handleMessage({type: 'noop', ref: sent[0].ref});
            expect(button.disabled).toBe(false);
        } finally { dom.window.close(); }
    });

    it('cancels one errored request without consuming the next', async () => {
        const {dom, transport, button, sent, send, reply} = setup(name);
        try {
            const first = send();
            const second = send();
            await transport.handleMessage({type: 'error', ref: sent[0].ref, error: 'rejected'});
            expect(await first).toBeNull();
            expect(button.disabled).toBe(true);
            expect(transport.lastTriggerElement).toBe(button);
            await reply(1);
            await second;
            expect(button.disabled).toBe(false);
        } finally { dom.window.close(); }
    });

    it('disconnect settles owned requests and restores loading', async () => {
        const {dom, transport, button, send, loading} = setup(name);
        try {
            if (transport.ws) transport.ws.close = vi.fn();
            const first = send();
            const second = send();
            transport.disconnect();
            expect(await first).toBeNull();
            expect(await second).toBeNull();
            expect(button.disabled).toBe(false);
            expect(loading.pendingEvents.size).toBe(0);
        } finally { dom.window.close(); }
    });

    it('retains each background batch independently of foreground acknowledgements', async () => {
        const {dom, transport, button, sent, send, loading} = setup(name);
        try {
            const first = send();
            await transport.handleMessage({type: 'noop', ref: sent[0].ref, async_pending: true, async_batch: 'batch-a'});
            await first;
            const second = send();
            await transport.handleMessage({type: 'noop', ref: sent[1].ref, async_pending: true, async_batch: 'batch-b'});
            await second;
            await transport.handleMessage({type: 'async_complete', async_batch: 'batch-b'});
            expect(button.disabled).toBe(true);
            await transport.handleMessage({type: 'async_complete', async_batch: 'batch-b'});
            expect(button.disabled).toBe(true);
            await transport.handleMessage({type: 'async_complete', async_batch: 'batch-a'});
            expect(button.disabled).toBe(false);
            expect(loading.pendingEvents.size).toBe(0);
        } finally { dom.window.close(); }
    });

    it('uses the child wrapper, not a removable event routing marker, as loading owner', async () => {
        const {dom, transport, button, sent, send} = setup(name);
        try {
            button.parentElement.setAttribute('dj-view', '');
            // live_render stamps routing hints onto event-bearing descendants.
            button.setAttribute('data-djust-embedded', 'child');
            send();
            await transport.handleMessage({type: 'embedded_update', ref: sent[0].ref,
                view_id: 'child', async_pending: true, async_batch: 'owned-wrapper',
                html: '<button id="save" dj-click="save" dj-loading.disable>Running</button>'});
            expect(button.hasAttribute('data-djust-embedded')).toBe(false);
            expect(button.disabled).toBe(true);
            await transport.handleMessage({type: 'async_complete', async_batch: 'owned-wrapper'});
            expect(button.disabled).toBe(false);
        } finally { dom.window.close(); }
    });

    it('a background batch cannot be completed by a different transport', async () => {
        const {dom, transport, button, sent, send} = setup(name);
        try {
            send();
            await transport.handleMessage({type: 'noop', ref: sent[0].ref, async_pending: true, async_batch: 'owned'});
            const other = new dom.window.djust[name]();
            await other.handleMessage({type: 'async_complete', async_batch: 'owned'});
            expect(button.disabled).toBe(true);
            await transport.handleMessage({type: 'async_complete', async_batch: 'owned'});
            expect(button.disabled).toBe(false);
        } finally { dom.window.close(); }
    });

    it('disconnect clears acknowledged background batches too', async () => {
        const {dom, transport, button, sent, send, loading} = setup(name);
        try {
            if (transport.ws) transport.ws.close = vi.fn();
            send();
            await transport.handleMessage({type: 'noop', ref: sent[0].ref, async_pending: true, async_batch: 'pending'});
            transport.disconnect();
            expect(button.disabled).toBe(false);
            expect(loading.pendingEvents.size).toBe(0);
        } finally { dom.window.close(); }
    });

    it('background errors do not cancel a newer foreground request', async () => {
        const {dom, transport, button, sent, send} = setup(name);
        try {
            send();
            await transport.handleMessage({type: 'noop', ref: sent[0].ref, async_pending: true, async_batch: 'failed'});
            let completed = false;
            send().then(() => { completed = true; });
            await transport.handleMessage({type: 'error', source: 'async', async_batch: 'failed', error: 'Task unavailable'});
            await transport.handleMessage({type: 'async_complete', async_batch: 'failed'});
            expect(completed).toBe(false);
            expect(button.disabled).toBe(true);
            await transport.handleMessage({type: 'noop', ref: sent[1].ref});
            expect(completed).toBe(true);
            expect(button.disabled).toBe(false);
        } finally { dom.window.close(); }
    });

    it('settles a valid scoped reply after the owner has been removed', async () => {
        const {dom, button, send, reply, loading} = setup(name);
        try {
            const response = send();
            button.parentElement.remove();
            await reply(0);
            expect((await response).type).toBe('embedded_update');
            expect(loading.pendingEvents.size).toBe(0);
            expect(dom.window.djust._getEventSeqState().pendingEventRefs).toEqual([]);
        } finally { dom.window.close(); }
    });

    it('old transport disconnect cannot settle replacement transport work', async () => {
        const {dom, transport, button, send} = setup(name);
        try {
            const replacement = new dom.window.djust[name]();
            replacement.viewMounted = true;
            replacement.ws = {readyState: dom.window.WebSocket.OPEN, send: vi.fn(), close: vi.fn()};
            replacement.sseBaseUrl = '/djust/sse/new/';
            if (transport.ws) transport.ws.close = vi.fn();
            const old = send();
            let newDone = false;
            replacement.sendEvent('save', {}, button).then(() => { newDone = true; });
            transport.disconnect();
            await old;
            expect(newDone).toBe(false);
            expect(button.disabled).toBe(true);
            replacement.disconnect();
            await Promise.resolve();
            expect(newDone).toBe(true);
            expect(button.disabled).toBe(false);
        } finally { dom.window.close(); }
    });
});

it('a failed SSE POST cancels only its request', async () => {
    const {dom, transport, button, send, sent, reply} = setup('LiveViewSSE');
    try {
        let rejectFirst;
        dom.window.fetch.mockImplementationOnce((_url, options) => {
            sent.push(JSON.parse(options.body));
            return new Promise((_resolve, reject) => { rejectFirst = reject; });
        });
        const first = send();
        const second = send();
        rejectFirst(new Error('offline'));
        expect(await first).toBeNull();
        expect(button.disabled).toBe(true);
        expect(transport.lastTriggerElement).toBe(button);
        await reply(1);
        await second;
        expect(button.disabled).toBe(false);
    } finally { dom.window.close(); }
});
