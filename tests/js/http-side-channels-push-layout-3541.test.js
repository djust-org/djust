// #3541 (backport): an HTTP fallback answer's _push_events and _layout reach
// the page. Before, handleServerResponse applied only _flash and
// _page_metadata from an HTTP answer, so push_event / push_commands and
// set_layout were ignored over HTTP even once the server sent them.
import {it, expect} from 'vitest';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');

function boot() {
    const dom = new JSDOM('<!doctype html><body><div dj-root><button id="go" dj-click="go">Go</button></div></body>', {
        url: 'http://localhost/', runScripts: 'dangerously',
    });
    dom.window.eval(client);
    return dom;
}

async function answer(dom, data) {
    dom.window.fetch = async () => ({ok: true, json: async () => data});
    const button = dom.window.document.getElementById('go');
    await dom.window.djust.handleEvent('go', {_targetElement: button});
}

it('dispatches each _push_events entry as a djust:push_event', async () => {
    const dom = boot();
    try {
        const seen = [];
        dom.window.addEventListener('djust:push_event', e => seen.push(e.detail));
        await answer(dom, {
            _push_events: [
                {event: 'chart:update', payload: {points: [1, 2]}},
                {event: 'toast', payload: {text: 'hi'}},
            ],
        });
        expect(seen).toEqual([
            {event: 'chart:update', payload: {points: [1, 2]}},
            {event: 'toast', payload: {text: 'hi'}},
        ]);
    } finally { dom.window.close(); }
});

it('applies _layout through djLayout.applyLayout', async () => {
    const dom = boot();
    try {
        const applied = [];
        dom.window.djust.djLayout = {applyLayout: payload => applied.push(payload)};
        await answer(dom, {_layout: {path: 'layouts/full.html', html: '<main dj-root>x</main>'}});
        expect(applied).toEqual([{path: 'layouts/full.html', html: '<main dj-root>x</main>'}]);
    } finally { dom.window.close(); }
});

it('does nothing for an answer without either key', async () => {
    const dom = boot();
    try {
        const seen = [];
        const applied = [];
        dom.window.addEventListener('djust:push_event', e => seen.push(e.detail));
        dom.window.djust.djLayout = {applyLayout: payload => applied.push(payload)};
        await answer(dom, {_flash: []});
        expect(seen).toEqual([]);
        expect(applied).toEqual([]);
    } finally { dom.window.close(); }
});
