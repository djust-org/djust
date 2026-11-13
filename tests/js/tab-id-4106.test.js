import {it, expect} from 'vitest';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';

// #4106: the page GET puts <meta name="djust-tab"> in the document; the client
// sends that id back on every fallback POST (and on its mount frames), so the
// server keeps one saved state per tab.
const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');

function page(head) {
    const dom = new JSDOM(
        `<!doctype html><head>${head}</head><body><div dj-root dj-view="app.View">`
        + '<p id="n">0</p><button id="go" dj-click="bump">Go</button></div></body>',
        {url: 'http://localhost/', runScripts: 'dangerously'},
    );
    dom.window.eval(client);
    return dom;
}

async function postedHeaders(dom) {
    const win = dom.window;
    let seen = null;
    win.fetch = async (_url, init) => {
        seen = init.headers;
        return {ok: true, json: async () => ({type: 'noop'})};
    };
    await win.djust.handleEvent('bump', {_targetElement: win.document.getElementById('go')});
    return seen;
}

it('the fallback POST carries the tab id the page was given', async () => {
    const dom = page('<meta name="djust-tab" content="tab-aaaaaaaa">');
    try {
        const headers = await postedHeaders(dom);
        expect(headers['X-Djust-Tab']).toBe('tab-aaaaaaaa');
        expect(headers['X-Djust-Event']).toBe('bump');
    } finally { dom.window.close(); }
});

it('after a navigation that merged a new head in, the newest tab id is the one sent', async () => {
    const dom = page(
        '<meta name="djust-tab" content="tab-old-page">'
        + '<meta name="djust-tab" content="tab-new-page">',
    );
    try {
        expect((await postedHeaders(dom))['X-Djust-Tab']).toBe('tab-new-page');
    } finally { dom.window.close(); }
});

it('a page without a tab id sends an empty one, which selects nothing', async () => {
    const dom = page('');
    try {
        expect((await postedHeaders(dom))['X-Djust-Tab']).toBe('');
    } finally { dom.window.close(); }
});

it('the socket mount frame, the SSE mount frames and the stream URL all read the same id', () => {
    const source = name => readFileSync(`./python/djust/static/djust/src/${name}`, 'utf8');
    expect(source('03-websocket.js')).toMatch(/type: 'mount',[\s\S]{0,120}tab: djustTabId\(\)/);
    expect(source('03b-sse.js').match(/tab: djustTabId\(\)/g)).toHaveLength(2);
    expect(source('03b-sse.js')).toContain("urlParams.set('_djust_tab', djustTabId())");
});
