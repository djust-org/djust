import {it, expect} from 'vitest';
import {JSDOM, VirtualConsole} from 'jsdom';
import {readFileSync} from 'node:fs';

// The page-POST fallback mounts a fresh view per request, so each answer is a
// whole page numbered by that request's own renderer: {html, version: 1}.
// Three such answers in a row must update the page each time and never fall
// into the version-mismatch reload.
const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');

function page() {
    const reloads = [];
    const virtualConsole = new VirtualConsole();
    virtualConsole.on('jsdomError', e => {
        if (String(e.message).includes('navigation')) reloads.push(e.message);
    });
    const dom = new JSDOM(
        '<!doctype html><body><div dj-root dj-view="app.View"><p id="n">0</p>'
        + '<button id="go" dj-click="bump">Go</button></div></body>',
        {url: 'http://localhost/', runScripts: 'dangerously', virtualConsole},
    );
    dom.window.eval(client);
    return {dom, reloads};
}

function answer(n) {
    // The shape LiveView.post() answers with for a fresh instance (no baseline).
    return {
        html: `<div dj-root="" dj-view="app.View" dj-id="0"><p id="n" dj-id="1">${n}</p>`
            + `<button id="go" dj-click="bump" dj-id="2">Go</button></div>`,
        version: 1,
    };
}

it('three whole-page answers numbered 1 each update the page without reloading', async () => {
    const {dom, reloads} = page();
    try {
        const win = dom.window;
        let reloaded = 0;
        try { win.location.reload = () => { reloaded += 1; }; } catch (_e) { /* non-writable */ }
        for (const n of [1, 2, 3]) {
            win.fetch = async () => ({ok: true, json: async () => answer(n)});
            await win.djust.handleEvent('bump', {_targetElement: win.document.getElementById('go')});
            expect(win.document.getElementById('n').textContent).toBe(String(n));
        }
        expect(reloaded).toBe(0);
        expect(reloads).toEqual([]);
    } finally { dom.window.close(); }
});

it('a patch answer is still checked against the cursor', async () => {
    const {dom} = page();
    try {
        const win = dom.window;
        win.fetch = async () => ({ok: true, json: async () => answer(1)});
        await win.djust.handleEvent('bump', {_targetElement: win.document.getElementById('go')});
        expect(win.document.getElementById('n').textContent).toBe('1');
        // v3 after v1: a dropped answer. It must not be applied.
        win.fetch = async () => ({ok: true, json: async () => ({
            patches: [{type: 'SetText', path: [0, 0], d: '1', text: '9'}], version: 3,
        })});
        await win.djust.handleEvent('bump', {_targetElement: win.document.getElementById('go')});
        expect(win.document.getElementById('n').textContent).toBe('1');
    } finally { dom.window.close(); }
});
