// #3541: an HTTP fallback answer's push events and layout reach the page.
// Before, the HTTP side channels applied only navigation, flash and page
// metadata, so push_event / push_commands and set_layout were ignored over
// HTTP even once the server sent them. Since #3658 the answer is a `render`
// frame and all of them are its `effects`, applied as on the socket wires.
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

it('dispatches each push_event effect as a djust:push_event', async () => {
    const dom = boot();
    try {
        const seen = [];
        dom.window.addEventListener('djust:push_event', e => seen.push(e.detail));
        await answer(dom, {type: 'render', effects: [
            {kind: 'push_event', event: 'chart:update', payload: {points: [1, 2]}},
            {kind: 'push_event', event: 'toast', payload: {text: 'hi'}},
        ]});
        expect(seen).toEqual([
            {event: 'chart:update', payload: {points: [1, 2]}},
            {event: 'toast', payload: {text: 'hi'}},
        ]);
    } finally { dom.window.close(); }
});

it('applies a layout effect through djLayout.applyLayout', async () => {
    const dom = boot();
    try {
        const applied = [];
        dom.window.djust.djLayout = {applyLayout: payload => applied.push(payload)};
        await answer(dom, {type: 'render', effects: [
            {kind: 'layout', path: 'layouts/full.html', html: '<main dj-root>x</main>'}]});
        expect(applied).toEqual([{kind: 'layout', path: 'layouts/full.html', html: '<main dj-root>x</main>'}]);
    } finally { dom.window.close(); }
});

it('does nothing for an answer without either effect', async () => {
    const dom = boot();
    try {
        const seen = [];
        const applied = [];
        dom.window.addEventListener('djust:push_event', e => seen.push(e.detail));
        dom.window.djust.djLayout = {applyLayout: payload => applied.push(payload)};
        await answer(dom, {type: 'render', effects: [{kind: 'flash', action: 'clear'}]});
        expect(seen).toEqual([]);
        expect(applied).toEqual([]);
    } finally { dom.window.close(); }
});
