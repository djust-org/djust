/** Real bundle transport dispatch for scoped child background frames. */
import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');

function environment() {
    const dom = new JSDOM('<!doctype html><html><body></body></html>', {
        url: 'http://localhost/', runScripts: 'dangerously',
    });
    const { window } = dom;
    window.CSS ??= {};
    window.CSS.escape ??= value => String(value).replace(/([^\w-])/g, '\\$1');
    window.eval(client);
    window.document.body.innerHTML =
        '<div data-djust-embedded="left"><span>old left</span></div>' +
        '<div data-djust-embedded="right"><button dj-click="other" ' +
        'dj-loading.disable>right action</button></div>';
    return dom;
}

describe.each(['LiveViewWebSocket', 'LiveViewSSE'])('%s child completion', transportName => {
    it('morphs only the selected child through the real inbound handler', async () => {
        const dom = environment();
        try {
            const { window } = dom;
            const transport = new window.djust[transportName]();
            const right = window.document.querySelector('[data-djust-embedded="right"]');
            await transport.handleMessage({
                type: 'embedded_update', source: 'async', view_id: 'left',
                event_name: 'begin', html: '<span>finished left</span>',
            });
            expect(window.document.querySelector('[data-djust-embedded="left"]').textContent)
                .toBe('finished left');
            expect(window.document.querySelector('[data-djust-embedded="right"]')).toBe(right);
            expect(right.textContent).toBe('right action');
        } finally {
            dom.window.close();
        }
    });

    it.each(['async', 'event'])('does not acknowledge a different child event for source=%s', async source => {
        const dom = environment();
        try {
            const { window } = dom;
            const transport = new window.djust[transportName]();
            const button = window.document.querySelector('button');
            const loading = window.djust.globalLoadingManager;
            loading.scanAndRegister();
            loading.startLoading('other', button);
            transport.lastEventName = 'other';
            transport.lastTriggerElement = button;
            await transport.handleMessage({
                type: 'embedded_update', source, view_id: 'left',
                event_name: 'begin', html: '<span>finished left</span>',
            });
            expect(button.disabled).toBe(true);
            expect(loading.pendingEvents.has('other')).toBe(true);
            expect(transport.lastEventName).toBe('other');
            expect(transport.lastTriggerElement).toBe(button);
        } finally {
            dom.window.close();
        }
    });

    it('acknowledges a matching no-ref child event', async () => {
        const dom = environment();
        try {
            const { window } = dom;
            const transport = new window.djust[transportName]();
            const button = window.document.querySelector('button');
            const loading = window.djust.globalLoadingManager;
            loading.scanAndRegister();
            loading.startLoading('other', button);
            transport.lastEventName = 'other';
            transport.lastTriggerElement = button;
            await transport.handleMessage({
                type: 'embedded_update', view_id: 'right', event_name: 'other',
                html: '<button dj-click="other" dj-loading.disable>done</button>',
            });
            expect(loading.pendingEvents.has('other')).toBe(false);
            expect(transport.lastEventName).toBeNull();
            expect(window.document.querySelector('button').disabled).toBe(false);
        } finally {
            dom.window.close();
        }
    });

    it('does not acknowledge a different no-ref event in the same child', async () => {
        const dom = environment();
        try {
            const { window } = dom;
            const transport = new window.djust[transportName]();
            const button = window.document.querySelector('button');
            transport.lastEventName = 'other';
            transport.lastTriggerElement = button;
            await transport.handleMessage({
                type: 'embedded_update', view_id: 'right', event_name: 'earlier',
                html: '<button dj-click="other" dj-loading.disable>done</button>',
            });
            expect(transport.lastEventName).toBe('other');
            expect(transport.lastTriggerElement).toBe(button);
        } finally {
            dom.window.close();
        }
    });

    it('acknowledges its no-ref event even when the morph removes the trigger', async () => {
        const dom = environment();
        try {
            const { window } = dom;
            const transport = new window.djust[transportName]();
            const button = window.document.querySelector('button');
            transport.lastEventName = 'other';
            transport.lastTriggerElement = button;
            await transport.handleMessage({
                type: 'embedded_update', view_id: 'right', event_name: 'other',
                html: '<p>Saved</p>',
            });
            expect(button.isConnected).toBe(false);
            expect(transport.lastEventName).toBeNull();
            expect(transport.lastTriggerElement).toBeNull();
        } finally {
            dom.window.close();
        }
    });
});

it('resolves the matching WS event promise without consuming a newer event', async () => {
    const dom = environment();
    try {
        const { window } = dom;
        const transport = new window.djust.LiveViewWebSocket();
        const sent = [];
        transport.ws = { readyState: window.WebSocket.OPEN, send: text => sent.push(JSON.parse(text)) };
        transport.viewMounted = true;
        let result = null;
        transport.sendEvent('begin', {view_id: 'left'}).then(frame => { result = frame; });
        const button = window.document.querySelector('button');
        transport.sendEvent('other', {view_id: 'right'}, button);
        const reply = {
            type: 'embedded_update', ref: sent[0].ref, view_id: 'left',
            event_name: 'begin', html: '<span>done</span>',
        };
        await transport.handleMessage(reply);
        expect(result).toBe(reply);
        expect(transport.lastEventName).toBe('other');
        expect(transport.lastTriggerElement).toBe(button);
    } finally {
        dom.window.close();
    }
});
