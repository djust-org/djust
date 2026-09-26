// The SSE mount applies the mount HTML to an HTTP-prerendered page by
// morphing it, as the WebSocket mount does (#1610). It used to stamp only
// dj-ids, so values that differ per mount stayed stale: ADR-034 interactive
// components get new identities on every mount, and every event on the page
// then targeted an identity the server no longer had.
import {it, expect} from 'vitest';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');

it('an SSE mount morphs prerendered markup to the mount HTML', async () => {
    const dom = new JSDOM(
        '<div dj-root dj-view="test.View"><div data-component-id="cmp_old"><button dj-click="toggle">Menu</button></div><p>prerender</p></div>',
        {url: 'http://localhost/', runScripts: 'dangerously'},
    );
    try {
        class MockEventSource {
            static CLOSED = 2;
            constructor() { this.readyState = 1; }
            close() {}
        }
        dom.window.EventSource = MockEventSource;
        dom.window.eval(client);
        const sse = new dom.window.djust.LiveViewSSE();
        sse.connect('test.View', {});
        const button = dom.window.document.querySelector('button');
        await sse.handleMessage({
            type: 'mount', view: 'test.View', version: 1, has_ids: true,
            html: '<div data-component-id="cmp_new" dj-id="1"><button dj-click="toggle" dj-id="2">Menu</button></div><p dj-id="3">mounted</p>',
        });
        const doc = dom.window.document;
        expect(doc.querySelector('[data-component-id]').getAttribute('data-component-id')).toBe('cmp_new');
        expect(doc.querySelector('p').textContent).toBe('mounted');
        expect(doc.querySelector('p').getAttribute('dj-id')).toBe('3');
        // Keyed/positional morph keeps the existing button node.
        expect(doc.querySelector('button')).toBe(button);
    } finally {
        dom.window.close();
    }
});
