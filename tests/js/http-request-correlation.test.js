import {it, expect} from 'vitest';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');

it.each(['headers', 'body', 'navigation'])('drops an outgoing-page HTTP response after replacement during %s', async boundary => {
    const dom = new JSDOM('<!doctype html><body><div dj-root><button id="save" dj-click="save" dj-loading.disable>Save</button></div></body>', {
        url: 'http://localhost/', runScripts: 'dangerously',
    });
    try {
        dom.window.eval(client);
        let release;
        const gate = new Promise(resolve => { release = resolve; });
        const data = {_page_metadata: [{title: 'stale'}]};
        dom.window.djust.pageMetadata = {handlePageMetadata: cmd => { dom.window.document.title = cmd.title; }};
        dom.window.fetch = boundary === 'headers'
            ? () => gate
            : async () => ({ok: true, json: () => gate});
        const button = dom.window.document.getElementById('save');
        dom.window.djust.globalLoadingManager.scanAndRegister();
        const pending = dom.window.djust.handleEvent('save', {_targetElement: button});
        await Promise.resolve();
        if (boundary === 'navigation') {
            dom.window.dispatchEvent(new dom.window.CustomEvent('djust:before-navigate'));
        } else {
            dom.window.document.querySelector('[dj-root]').outerHTML = '<div dj-root>New page</div>';
        }
        dom.window.document.title = 'New page';
        release(boundary === 'headers' ? {ok: true, json: async () => data} : data);
        await pending;
        expect(dom.window.document.title).toBe('New page');
        expect(dom.window.djust._getEventSeqState().pendingEventRefs).toEqual([]);
    } finally { dom.window.close(); }
});

it.each([false, true])('HTTP completion releases only its own loading request (failure=%s)', async failure => {
    const dom = new JSDOM('<!doctype html><body><button id="save" dj-click="save" dj-loading.disable>Save</button></body>', {
        url: 'http://localhost/', runScripts: 'dangerously',
    });
    try {
        dom.window.eval(client);
        const requests = [];
        dom.window.fetch = () => new Promise(resolve => requests.push(resolve));
        const button = dom.window.document.getElementById('save');
        dom.window.djust.globalLoadingManager.scanAndRegister();
        const first = dom.window.djust.handleEvent('save', {_targetElement: button});
        const second = dom.window.djust.handleEvent('save', {_targetElement: button});
        expect(requests.length).toBe(2);
        requests[1]({ok: !failure, status: failure ? 500 : 200, json: async () => ({})});
        await second;
        expect(button.disabled).toBe(true);
        requests[0]({ok: true, json: async () => ({})});
        await first;
        expect(button.disabled).toBe(false);
        expect(dom.window.djust._getEventSeqState().pendingEventRefs).toEqual([]);
    } finally {
        dom.window.close();
    }
});

it('a cache hit cannot clear a concurrent HTTP request on the same control', async () => {
    const dom = new JSDOM('<!doctype html><body><button id="save" dj-click="save" dj-loading.disable>Save</button></body>', {
        url: 'http://localhost/', runScripts: 'dangerously',
    });
    try {
        dom.window.eval(client);
        const transport = new dom.window.djust.LiveViewWebSocket();
        await transport.handleMessage({type: 'mount', version: 1, cache_config: {save: {ttl: 60, key_params: ['value']}}});
        let fetches = 0;
        dom.window.fetch = async (_url, options) => {
            fetches += 1;
            return {ok: true, json: async () => ({
                cache_request_id: JSON.parse(options.body)._cacheRequestId, patches: [],
            })};
        };
        const button = dom.window.document.getElementById('save');
        dom.window.djust.globalLoadingManager.scanAndRegister();
        await dom.window.djust.handleEvent('save', {_targetElement: button, value: 'cached'});
        let release;
        dom.window.fetch = () => {
            fetches += 1;
            return new Promise(resolve => { release = resolve; });
        };
        const pending = dom.window.djust.handleEvent('save', {_targetElement: button, value: 'uncached'});
        await dom.window.djust.handleEvent('save', {_targetElement: button, value: 'cached'});
        expect(fetches).toBe(2);
        expect(button.disabled).toBe(true);
        release({ok: true, json: async () => ({})});
        await pending;
        expect(button.disabled).toBe(false);
        expect(dom.window.djust._getEventSeqState().pendingEventRefs).toEqual([]);
    } finally { dom.window.close(); }
});
