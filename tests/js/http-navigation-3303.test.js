import {it, expect} from 'vitest';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';

// #3303: the HTTP event fallback hands a queued navigation to the same
// dispatcher the WebSocket uses. Since #3658 the answer is a `render` frame and
// each navigation is one of its `effects`, the same on every wire.
const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');

function page() {
    const dom = new JSDOM(
        '<!doctype html><body><div dj-root><button id="go" dj-click="go">Go</button></div></body>',
        {url: 'http://localhost/', runScripts: 'dangerously'},
    );
    dom.window.eval(client);
    return dom;
}

it('dispatches each navigation effect of an HTTP answer to handleNavigation', async () => {
    const dom = page();
    try {
        const seen = [];
        dom.window.djust.navigation.handleNavigation = frame => seen.push(frame);
        const frames = [
            {kind: 'navigation', action: 'live_patch', replace: false, params: {q: '1'}},
            {kind: 'navigation', action: 'live_redirect', path: '/x/', replace: false},
        ];
        dom.window.fetch = async () => ({ok: true, json: async () => ({type: 'render', effects: frames})});
        await dom.window.djust.handleEvent('go', {_targetElement: dom.window.document.getElementById('go')});
        expect(seen).toEqual(frames);
    } finally { dom.window.close(); }
});

it('an answer without a navigation effect navigates nowhere', async () => {
    const dom = page();
    try {
        const seen = [];
        dom.window.djust.navigation.handleNavigation = frame => seen.push(frame);
        dom.window.fetch = async () => ({ok: true, json: async () => ({type: 'render', effects: []})});
        await dom.window.djust.handleEvent('go', {_targetElement: dom.window.document.getElementById('go')});
        expect(seen).toEqual([]);
    } finally { dom.window.close(); }
});

it('a live_patch effect moves the URL without a mounted socket', async () => {
    const dom = page();
    try {
        dom.window.fetch = async () => ({
            ok: true,
            json: async () => ({type: 'render', effects: [
                {kind: 'navigation', action: 'live_patch', replace: false, path: '/list/', params: {q: 'a'}},
            ]}),
        });
        await dom.window.djust.handleEvent('go', {_targetElement: dom.window.document.getElementById('go')});
        expect(dom.window.location.pathname + dom.window.location.search).toBe('/list/?q=a');
    } finally { dom.window.close(); }
});
